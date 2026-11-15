"""Read Copernicus EMS rapid-mapping vector packages (zip of shapefiles).

Package layouts vary between activations and years, so layers are recognised by content, not by an
exact file name:
  * perimeter: polygon layers whose name contains "observed_event" (the burned area)
  * damage:    building layers ("built_up") with a damage-grade attribute. Land-use and transport
               layers carry the same attribute, but they are not buildings and are left out.
Every layer found is reported, so an unexpected package can be diagnosed from the printout.
"""
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

DAMAGE_FIELDS = ("damage_gra", "grading", "dmg_src_gr", "damage", "dmg_gra", "obj_damage")

# Copernicus grades, oldest and newest wording, folded into four classes.
GRADE_ORDER = ("destroyed", "damaged", "possibly_damaged", "no_visible_damage")


def normalise_grade(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    if not v:
        return None
    if "destroy" in v:
        return "destroyed"
    if "possib" in v or "moderate" in v:
        return "possibly_damaged"
    if "no visible" in v or "negligible" in v or v in ("not affected", "no damage", "none"):
        return "no_visible_damage"
    if "damage" in v or "high" in v:
        return "damaged"
    return None


@dataclass
class LayerInfo:
    name: str
    geometry: str
    count: int
    fields: list[str]
    crs_note: str = ""


@dataclass
class PackageContents:
    layers: list[LayerInfo] = field(default_factory=list)
    perimeter: list[Any] = field(default_factory=list)      # shapely geometries
    damage: list[dict[str, Any]] = field(default_factory=list)  # GeoJSON features


def _looks_geographic(prj: str) -> bool:
    return prj == "" or prj.upper().startswith("GEOGCS")


def _check_lonlat(geom, layer: str) -> None:
    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx <= 180 and -90 <= miny <= 90 and -180 <= maxx <= 180 and -90 <= maxy <= 90):
        raise ValueError(f"layer {layer} is not in lon/lat degrees (bounds {geom.bounds}); "
                         "a reprojection step is needed")


def read_package(data: bytes) -> PackageContents:
    import shapefile  # pyshp

    out = PackageContents()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        for shp in sorted(n for n in names if n.lower().endswith(".shp")):
            stem = shp[:-4]
            parts = {ext: zf.read(stem + ext) for ext in (".shp", ".shx", ".dbf") if stem + ext in names}
            if len(parts) < 3:
                continue
            prj = zf.read(stem + ".prj").decode("utf-8", "replace") if stem + ".prj" in names else ""
            reader = shapefile.Reader(
                shp=io.BytesIO(parts[".shp"]), shx=io.BytesIO(parts[".shx"]), dbf=io.BytesIO(parts[".dbf"]),
                encoding="utf-8", encodingErrors="replace")
            fields = [f[0] for f in reader.fields[1:]]
            layer = Path(stem).name
            records = list(reader.iterShapeRecords())
            gtype = reader.shapeTypeName
            out.layers.append(LayerInfo(layer, gtype, len(records), fields,
                                        "" if _looks_geographic(prj) else "projected CRS"))
            lname = layer.lower()
            grade_field = next((f for f in fields if f.lower() in DAMAGE_FIELDS), None)
            for sr in records:
                if sr.shape.shapeType == 0:  # null shape
                    continue
                geom = shape(sr.shape.__geo_interface__)
                if geom.is_empty:
                    continue
                _check_lonlat(geom, layer)
                props = dict(zip(fields, sr.record, strict=False))
                if "observed_event" in lname and geom.geom_type in ("Polygon", "MultiPolygon"):
                    out.perimeter.append(geom if geom.is_valid else geom.buffer(0))
                elif grade_field and grade_field in props and "built_up" in lname:
                    grade = normalise_grade(str(props[grade_field]))
                    if grade is None:
                        continue
                    c = geom if geom.geom_type == "Point" else geom.representative_point()
                    out.damage.append({"type": "Feature",
                                       "geometry": {"type": "Point", "coordinates": [c.x, c.y]},
                                       "properties": {"grade": grade, "raw": str(props[grade_field]),
                                                      "obj_type": str(props.get("obj_type", "")),
                                                      "layer": layer}})
    return out


def perimeter_geojson(contents: PackageContents, source: str) -> dict[str, Any]:
    if not contents.perimeter:
        raise ValueError("no burned-area polygons ('observed_event') found in the package")
    geom = unary_union(contents.perimeter)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(geom), "properties": {"source": source}}]}


def damage_geojson(contents: PackageContents, source: str) -> dict[str, Any]:
    feats = [f for f in contents.damage if f["properties"]["grade"] != "no_visible_damage"]
    return {"type": "FeatureCollection", "source": source, "features": feats}


def describe(contents: PackageContents) -> str:
    lines = []
    for li in contents.layers:
        note = f" [{li.crs_note}]" if li.crs_note else ""
        lines.append(f"    {li.name}: {li.geometry} x{li.count}{note}  fields={li.fields}")
    return "\n".join(lines)


def dumps(fc: dict[str, Any]) -> str:
    # Round coordinates to ~1 m to keep files small.
    def rnd(o):
        if isinstance(o, float):
            return round(o, 6)
        if isinstance(o, list | tuple):
            return [rnd(x) for x in o]
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        return o
    return json.dumps(rnd(fc), ensure_ascii=False)

