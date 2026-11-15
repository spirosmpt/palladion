"""Load a scenario folder: scenario.yaml plus any processed data the pipeline produced."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from shapely.geometry import LineString, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import split, unary_union

from palladion.domain.models import Confidence, SafeZone, ScenarioSpec, ZoneKind


@dataclass
class ScenarioData:
    spec: ScenarioSpec
    land: BaseGeometry               # where fire and people can be (lon/lat)
    land_source: str                 # "osm" or "fallback"
    roads: dict[str, Any] | None     # GeoJSON FeatureCollection of road lines, if processed
    buildings: dict[str, Any] | None # GeoJSON FeatureCollection of building footprints, if processed
    perimeter: BaseGeometry | None   # observed burned area (Copernicus EMS), if processed
    damage: dict[str, Any] | None    # Copernicus building damage grades, if processed
    zones: list[SafeZone]
    zones_source: str


def load_spec(scenario_dir: Path) -> ScenarioSpec:
    raw = yaml.safe_load((scenario_dir / "scenario.yaml").read_text(encoding="utf-8"))
    return ScenarioSpec.model_validate(raw)


def _read_geojson(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def land_from_coastline(bbox: tuple[float, float, float, float], coast: list[tuple[float, float]],
                        inland_point: tuple[float, float]) -> BaseGeometry:
    """Split the bbox with a coastline and keep the side that contains `inland_point`."""
    frame = box(*bbox)
    pieces = split(frame, LineString(coast))
    from shapely.geometry import Point
    probe = Point(inland_point)
    for piece in getattr(pieces, "geoms", [pieces]):
        if piece.contains(probe):
            return piece
    raise ValueError("coastline does not separate the inland point from the sea inside the bbox")


def load_scenario(scenario_dir: Path, data_dir: Path) -> ScenarioData:
    spec = load_spec(scenario_dir)
    ignition = (spec.fire.ignition.lon, spec.fire.ignition.lat)

    land_fc = _read_geojson(data_dir / "land.geojson")
    if land_fc and land_fc.get("features"):
        land = unary_union([shape(f["geometry"]) for f in land_fc["features"]])
        land_source = "osm"
    else:
        land = land_from_coastline(spec.bbox, spec.fallback_coastline, ignition)
        land_source = "fallback"
    if isinstance(land, Polygon) and not land.is_valid:
        land = land.buffer(0)

    roads = _read_geojson(data_dir / "roads.geojson")
    buildings = _read_geojson(data_dir / "buildings.geojson")
    perimeter_fc = _read_geojson(data_dir / "perimeter.geojson")
    perimeter = (unary_union([shape(f["geometry"]) for f in perimeter_fc["features"]]).buffer(0)
                 if perimeter_fc and perimeter_fc.get("features") else None)
    damage = _read_geojson(data_dir / "damage.geojson")

    zones_fc = _read_geojson(data_dir / "zones.geojson")
    if zones_fc and zones_fc.get("features"):
        zones = []
        for f in zones_fc["features"]:
            p = f["properties"]
            c = shape(f["geometry"]).centroid
            zones.append(SafeZone(
                id=str(p["id"]), name=p.get("name") or p.get("kind", "zone"),
                kind=ZoneKind(p.get("kind", "open_space")), lon=c.x, lat=c.y,
                capacity_estimate=p.get("capacity_estimate"),
                confidence=Confidence(p.get("confidence", "approximate")), note=p.get("note", ""),
            ))
        zones_source = "osm"
    else:
        zones, zones_source = list(spec.fallback_zones), "fallback"

    return ScenarioData(spec=spec, land=land, land_source=land_source, roads=roads, buildings=buildings,
                        perimeter=perimeter, damage=damage,
                        zones=zones, zones_source=zones_source)
