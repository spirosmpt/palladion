"""Download and process the open data for a scenario.

    python -m palladion.datasets.fetch                 # Mati 2018, everything
    python -m palladion.datasets.fetch --only weather
    python -m palladion.datasets.fetch --only coast    # cliffs, beaches and elevation (shore access)

Writes into data/processed/ (see config.Settings.data_dir). Needs internet and the `pipeline` extra.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from palladion.config import get_settings
from palladion.datasets import copernicus, dem, osm
from palladion.scenarios.loader import load_spec

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HEADERS = {"User-Agent": "palladion/0.1 (wildfire evacuation research; github.com/LamboKiller)"}


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def _overpass(query: str) -> list[dict[str, Any]]:
    import requests
    last_error: Exception | None = None
    for url in OVERPASS:
        try:
            print(f"Overpass: {url}")
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=240)
            r.raise_for_status()
            elements = r.json()["elements"]
            break
        except Exception as exc:  # try the next mirror
            last_error = exc
            print(f"  failed: {exc}")
            time.sleep(2)
    else:
        raise SystemExit(f"Overpass unavailable: {last_error}")
    print(f"  {len(elements)} elements")
    return elements


def fetch_osm(spec, out: Path) -> None:
    elements = _overpass(osm.overpass_query(spec.bbox))
    _write(out / "osm_raw.json", {"bbox": spec.bbox, "elements": elements})
    process_osm(spec, out)


def fetch_buildings(spec, out: Path) -> None:
    bbox = spec.detail_bbox or spec.bbox
    print(f"Buildings in {bbox}")
    buildings = osm.buildings_from_overpass(_overpass(osm.buildings_query(bbox)))
    print(f"  buildings: {len(buildings['features'])}")
    _write(out / "buildings.geojson", buildings)


def fetch_coast(spec, out: Path) -> None:
    """Shore access: cliffs and beaches mapped in OSM, and elevation tiles to find steep shores OSM misses."""
    import requests
    bbox = spec.detail_bbox or spec.bbox
    print(f"Cliffs and beaches in {bbox}")
    _write(out / "cliffs.geojson", osm.cliffs_from_overpass(_overpass(osm.cliffs_query(bbox))))
    xs, ys = dem.tile_range(bbox)
    print(f"Elevation tiles: {len(xs) * len(ys)} at zoom {dem.ZOOM}")
    tiles = {}
    for x in xs:
        for y in ys:
            url = dem.TILE_URL.format(z=dem.ZOOM, x=x, y=y)
            for attempt in range(3):
                try:
                    r = requests.get(url, headers=HEADERS, timeout=60)
                    r.raise_for_status()
                    tiles[(x, y)] = dem.decode_terrarium(r.content)
                    break
                except Exception as exc:  # retry, then give up on the whole step
                    if attempt == 2:
                        raise SystemExit(f"elevation tile {url} failed: {exc}") from exc
                    time.sleep(2)
    grid = dem.Dem.from_tiles(tiles)
    path = out / "dem.npz"
    grid.save(path)
    print(f"  wrote {path} ({path.stat().st_size / 1024:.0f} KB), "
          f"elevation {np.nanmin(grid.elev):.0f}..{np.nanmax(grid.elev):.0f} m")


def process_osm(spec, out: Path) -> None:
    elements = json.loads((out / "osm_raw.json").read_text(encoding="utf-8"))["elements"]
    inland = (spec.fire.ignition.lon, spec.fire.ignition.lat)
    roads = osm.roads_from_overpass(elements)
    land = osm.land_from_overpass(elements, spec.bbox, inland)
    zones = osm.zones_from_overpass(elements, land)
    places = osm.places_from_overpass(elements)
    print(f"  roads: {len(roads['features'])} edges, zones: {len(zones['features'])}, "
          f"places: {len(places['features'])}")
    _write(out / "roads.geojson", roads)
    _write(out / "land.geojson", land)
    _write(out / "zones.geojson", zones)
    _write(out / "places.geojson", places)


def fetch_copernicus(spec, out: Path) -> None:
    """Burned-area perimeter and building damage grades from Copernicus EMS rapid mapping."""
    import requests
    cfg = spec.copernicus
    if cfg is None:
        print("No Copernicus activation configured for this scenario; skipping.")
        return
    raw = out.parent / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for kind, url in (("delineation", cfg.delineation_zip), ("grading", cfg.grading_zip)):
        if not url:
            continue
        print(f"Copernicus {cfg.activation} {kind}: {url}")
        r = requests.get(url, headers=HEADERS, timeout=180)
        r.raise_for_status()
        (raw / Path(url).name).write_bytes(r.content)
        contents = copernicus.read_package(r.content)
        print("  layers:\n" + copernicus.describe(contents))
        if kind == "delineation":
            fc = copernicus.perimeter_geojson(contents, f"{cfg.activation} {kind}")
            (out / "perimeter.geojson").write_text(copernicus.dumps(fc), encoding="utf-8")
            print(f"  wrote {out / 'perimeter.geojson'}")
        else:
            fc = copernicus.damage_geojson(contents, f"{cfg.activation} {kind}")
            if contents.perimeter and not (out / "perimeter.geojson").exists():
                pf = copernicus.perimeter_geojson(contents, f"{cfg.activation} {kind}")
                (out / "perimeter.geojson").write_text(copernicus.dumps(pf), encoding="utf-8")
            (out / "damage.geojson").write_text(copernicus.dumps(fc), encoding="utf-8")
            grades: dict[str, int] = {}
            types: dict[str, int] = {}
            for f in fc["features"]:
                grades[f["properties"]["grade"]] = grades.get(f["properties"]["grade"], 0) + 1
                types[f["properties"]["obj_type"]] = types.get(f["properties"]["obj_type"], 0) + 1
            print(f"  wrote {out / 'damage.geojson'}: {grades or 'no graded buildings found'}")
            print(f"  building types (obj_type): {types}")


def fetch_weather(spec, out: Path) -> None:
    """Hourly reanalysis (ERA5 via Open-Meteo) at the ignition and at the coast."""
    import requests
    day = spec.start.date().isoformat()
    points = {"ignition": spec.fire.ignition, **{a.id: a for a in spec.fire.anchors}}
    result = {"source": "Open-Meteo historical weather API (ERA5 reanalysis)", "date": day, "points": {}}
    for name, p in points.items():
        r = requests.get(OPEN_METEO_ARCHIVE, headers=HEADERS, timeout=60, params={
            "latitude": p.lat, "longitude": p.lon, "start_date": day, "end_date": day,
            "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,relative_humidity_2m",
            "timezone": spec.timezone})
        r.raise_for_status()
        result["points"][name] = {"lon": p.lon, "lat": p.lat, "hourly": r.json()["hourly"]}
        print(f"Open-Meteo: {name} ok")
    _write(out / f"weather_{day}.json", result)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["osm", "buildings", "copernicus", "weather", "coast", "process"],
                    help="run one step only")
    args = ap.parse_args(argv)
    settings = get_settings()
    spec = load_spec(settings.scenario_dir)
    out = settings.data_dir
    print(f"Scenario {spec.id}, bbox {spec.bbox} -> {out}")
    if args.only in (None, "osm"):
        fetch_osm(spec, out)
    if args.only in (None, "buildings"):
        fetch_buildings(spec, out)
    if args.only in (None, "copernicus"):
        fetch_copernicus(spec, out)
    if args.only == "process":
        process_osm(spec, out)
    if args.only in (None, "weather"):
        fetch_weather(spec, out)
    if args.only in (None, "coast"):
        fetch_coast(spec, out)
    print("Done. Restart the API to load the new data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
