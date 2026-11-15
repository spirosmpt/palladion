"""Turn an Overpass JSON response into the processed layers the engines read.

Pure functions, no network: fetch.py downloads, this module transforms, tests use fixtures.
"""
import re
from collections import Counter
from typing import Any

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping
from shapely.ops import linemerge, split, unary_union

ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                "residential", "living_street", "service", "track")

FeatureCollection = dict[str, Any]
ALT_NAME_KEYS = ("alt_name", "old_name", "loc_name", "name:el-Latn")


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    b = f"{s},{w},{n},{e}"
    road_re = "^(" + "|".join(ROAD_CLASSES) + ")(_link)?$"
    return f"""[out:json][timeout:180];
(
  way["highway"~"{road_re}"]({b});
  way["natural"="coastline"]({b});
  way["natural"="beach"]({b});
  way["leisure"~"^(pitch|stadium|park|marina)$"]({b});
  way["landuse"="port"]({b});
  node["place"~"^(town|village|suburb|neighbourhood|hamlet|locality)$"]({b});
  node["amenity"~"^(hospital|clinic|fire_station|police)$"]({b});
  way["amenity"~"^(hospital|clinic|fire_station|police)$"]({b});
);
out body geom;
"""


def _coords(way: dict[str, Any]) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in way.get("geometry", [])]


def _maxspeed(tags: dict[str, str]) -> float | None:
    m = re.match(r"^\s*(\d+)", tags.get("maxspeed", ""))
    return float(m.group(1)) if m else None


def roads_from_overpass(elements: list[dict[str, Any]]) -> FeatureCollection:
    """Road ways split at every node shared with another road, so each feature is one graph edge."""
    ways = [e for e in elements if e.get("type") == "way" and "highway" in e.get("tags", {})
            and e["tags"]["highway"].removesuffix("_link") in ROAD_CLASSES]
    use = Counter(n for w in ways for n in w.get("nodes", []))
    feats = []
    for w in ways:
        tags, nodes, coords = w["tags"], w.get("nodes", []), _coords(w)
        if len(coords) < 2 or len(coords) != len(nodes):
            continue
        oneway_tag = tags.get("oneway", "no")
        oneway = oneway_tag in ("yes", "1", "true", "-1") or tags.get("junction") == "roundabout"
        if oneway_tag == "-1":
            coords, nodes = coords[::-1], nodes[::-1]
        start = 0
        piece = 0
        for i in range(1, len(nodes)):
            if i == len(nodes) - 1 or use[nodes[i]] > 1:
                seg = coords[start:i + 1]
                if len(seg) >= 2:
                    feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": seg},
                                  "properties": {
                                      "id": f"w{w['id']}_{piece}", "osm_way": w["id"],
                                      "highway": tags["highway"].removesuffix("_link"),
                                      "name": tags.get("name:el") or tags.get("name") or "",
                                      "name_en": tags.get("name:en", ""), "oneway": oneway,
                                      "speed_kmh": None, "maxspeed": _maxspeed(tags)}})
                    piece += 1
                start = i
    return {"type": "FeatureCollection", "features": feats}


def land_from_overpass(elements: list[dict[str, Any]], bbox: tuple[float, float, float, float],
                       inland: tuple[float, float]) -> FeatureCollection:
    """Land polygon from coastline ways: split the bbox, keep the inland side, add closed islands."""
    lines = [LineString(_coords(e)) for e in elements
             if e.get("type") == "way" and e.get("tags", {}).get("natural") == "coastline"
             and len(e.get("geometry", [])) >= 2]
    if not lines:
        raise ValueError("no coastline in the area; check the bbox")
    frame = box(*bbox)
    union = unary_union(lines)
    merged = linemerge(union) if union.geom_type == "MultiLineString" else union
    parts = list(getattr(merged, "geoms", [merged]))
    islands = [Polygon(p.coords) for p in parts if p.is_ring and len(p.coords) >= 4]
    open_lines = [p for p in parts if not p.is_ring]
    pieces = [frame]
    for line in open_lines:
        pieces = [g for piece in pieces for g in getattr(split(piece, line), "geoms", [piece])]
    probe = Point(inland)
    main = [p for p in pieces if p.contains(probe)]
    if not main:
        raise ValueError("the inland point is not on land after splitting by the coastline")
    land = unary_union([main[0], *[i.intersection(frame) for i in islands]])
    if isinstance(land, Polygon):
        land = MultiPolygon([land])
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": mapping(land), "properties": {"source": "osm"}}]}


def zones_from_overpass(elements: list[dict[str, Any]], land_fc: FeatureCollection,
                        m2_per_person: float = 2.0) -> FeatureCollection:
    """Candidate refuges: beaches, ports/marinas and large open spaces. Capacities are rough estimates."""
    from shapely.geometry import shape

    from palladion.geo.proj import LocalFrame
    land = unary_union([shape(f["geometry"]) for f in land_fc["features"]])
    feats = []
    for e in elements:
        if e.get("type") != "way":
            continue
        tags, coords = e.get("tags", {}), _coords(e)
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue
        if tags.get("natural") == "beach":
            kind = "beach"
        elif tags.get("leisure") == "marina" or tags.get("landuse") == "port":
            kind = "port"
        elif tags.get("leisure") in ("pitch", "stadium", "park"):
            kind = "open_space"
        else:
            continue
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        c = poly.centroid
        frame = LocalFrame(c.x, c.y)
        area = Polygon([frame.to_xy(x, y) for x, y in poly.exterior.coords]).area
        if kind == "open_space" and area < 5_000:
            continue
        # A beach polygon may lie partly in the sea; anchor it to its nearest land point for routing.
        anchor = c if land.contains(c) else land.boundary.interpolate(land.boundary.project(c))
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [anchor.x, anchor.y]},
                      "properties": {"id": f"w{e['id']}", "kind": kind,
                                     "name": tags.get("name:el") or tags.get("name") or "",
                                     "area_m2": round(area), "capacity_estimate": int(area / m2_per_person),
                                     "confidence": "approximate",
                                     "note": f"Χωρητικότητα: εμβαδόν / {m2_per_person:g} m² ανά άτομο."}})
    return {"type": "FeatureCollection", "features": feats}


def places_from_overpass(elements: list[dict[str, Any]]) -> FeatureCollection:
    """Named places and emergency facilities, for the map and (later) the location agent."""
    feats = []
    for e in elements:
        tags = e.get("tags", {})
        kind = tags.get("place") or tags.get("amenity")
        if not kind or kind not in ("town", "village", "suburb", "neighbourhood", "hamlet", "locality",
                                    "hospital", "clinic", "fire_station", "police"):
            continue
        if e["type"] == "node":
            lon, lat = e["lon"], e["lat"]
        else:
            coords = _coords(e)
            if not coords:
                continue
            c = LineString(coords).centroid if len(coords) > 1 else Point(coords[0])
            lon, lat = c.x, c.y
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"id": f"{e['type'][0]}{e['id']}", "kind": kind,
                                     "name": tags.get("name:el") or tags.get("name") or "",
                                     "name_en": tags.get("name:en", ""),
                                     "alt_names": [v for k, v in tags.items() if k in ALT_NAME_KEYS]}})
    return {"type": "FeatureCollection", "features": feats}


def cliffs_query(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    return f"""[out:json][timeout:120];
(
  way["natural"="cliff"]({s},{w},{n},{e});
  way["natural"~"^(beach|bare_rock)$"]({s},{w},{n},{e});
);
out body geom;
"""


def cliffs_from_overpass(elements: list[dict[str, Any]]) -> FeatureCollection:
    """Mapped cliffs (lines) and beaches (outlines), as mapped by OSM contributors."""
    feats = []
    for e in elements:
        tags = e.get("tags", {})
        kind = tags.get("natural")
        coords = _coords(e)
        if e.get("type") != "way" or len(coords) < 2 or kind not in ("cliff", "beach"):
            continue
        feats.append({"type": "Feature", "properties": {"kind": kind, "name": tags.get("name", "")},
                      "geometry": {"type": "LineString", "coordinates": coords}})
    return {"type": "FeatureCollection", "features": feats}


def buildings_query(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    return f"""[out:json][timeout:180];
way["building"]({s},{w},{n},{e});
out body geom;
"""


def _height_m(tags: dict[str, str]) -> float:
    """Height from OSM tags; otherwise a typical one- to two-storey Greek holiday home."""
    for key in ("height", "building:height"):
        m = re.match(r"^\s*([\d.]+)", tags.get(key, ""))
        if m:
            return float(m.group(1))
    m = re.match(r"^\s*(\d+)", tags.get("building:levels", ""))
    if m:
        return int(m.group(1)) * 3.2
    return 6.0


def buildings_from_overpass(elements: list[dict[str, Any]]) -> FeatureCollection:
    feats = []
    for e in elements:
        if e.get("type") != "way" or "building" not in e.get("tags", {}):
            continue
        coords = [(round(x, 6), round(y, 6)) for x, y in _coords(e)]
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue
        tags = e["tags"]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]},
                      "properties": {"id": f"w{e['id']}", "kind": tags["building"],
                                     "height": round(_height_m(tags), 1)}})
    return {"type": "FeatureCollection", "features": feats}
