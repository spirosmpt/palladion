import json

from fastapi.testclient import TestClient

from palladion.api.app import create_app
from palladion.config import REPO_ROOT, Settings
from palladion.geo.proj import LocalFrame


def client(tmp_path, roads=None):
    tmp_path = tmp_path / ("with_roads" if roads else "no_roads")
    data = tmp_path / "data"
    data.mkdir(parents=True)
    if roads:
        (data / "roads.geojson").write_text(json.dumps(roads), encoding="utf-8")
    s = Settings(scenario_dir=REPO_ROOT / "scenarios" / "mati_2018", data_dir=data,
                 runtime_dir=tmp_path / "runtime")
    return TestClient(create_app(s))


def mati_grid():
    """A small road ladder from Mati south to the Rafina port, in the scenario's real area."""
    f = LocalFrame(24.0113, 38.0210)            # Rafina port
    feats = []
    pts = [f.to_lonlat(-600 + 0, 300 * k) for k in range(0, 12)]     # a road north from the port
    for a, b in zip(pts, pts[1:], strict=False):
        feats.append({"type": "Feature", "properties": {"highway": "primary"},
                      "geometry": {"type": "LineString", "coordinates": [a, b]}})
    feats.append({"type": "Feature", "properties": {"highway": "service"},
                  "geometry": {"type": "LineString", "coordinates": [pts[0], [24.0113, 38.0210]]}})
    return {"type": "FeatureCollection", "features": feats}, pts


def test_scenario_and_fire_endpoints(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/health").json()["ok"]
        sc = c.get("/api/scenario").json()
        assert sc["spec"]["id"] == "mati_2018" and sc["data"]["land"] == "fallback"
        front = c.get("/api/fire/front", params={"t": "2018-07-23T18:35:00+03:00"}).json()
        assert front["geometry"] is not None
        assert 800 < front["properties"]["area_ha"] < 2000      # S1: 1,431 ha burned in total
        assert c.get("/api/fire/front", params={"t": "2018-07-23T16:35:00+03:00"}).json()["geometry"] is None
        iso = c.get("/api/fire/isochrones").json()
        labels = [f["properties"]["label"] for f in iso["features"]]
        assert labels[0] == "16:45" and labels[-2:] == ["18:15", "18:35"]
        assert len(c.get("/api/anchors").json()["features"]) == 3
        assert c.get("/api/buildings").json()["available"] is False
        assert front["properties"]["buildings_reached"] is None


def test_clock_commands(tmp_path):
    with client(tmp_path) as c:
        r = c.post("/api/clock", json={"action": "seek", "time": "2018-07-23T17:00:00+03:00"}).json()
        assert r["sim_time"].startswith("2018-07-23T17:00")
        assert c.post("/api/clock", json={"action": "speed", "speed": 120}).json()["speed"] == 120
        assert c.post("/api/clock", json={"action": "seek"}).status_code == 422


def test_evacuate_needs_roads_then_routes(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/api/route/evacuate", json={"lon": 23.99, "lat": 38.04}).status_code == 409
    roads, pts = mati_grid()
    with client(tmp_path, roads) as c:
        assert c.get("/api/roads").json()["available"]
        r = c.post("/api/route/evacuate", json={"lon": pts[5][0], "lat": pts[5][1],
                                                "depart": "2018-07-23T17:30:00+03:00"}).json()
        assert r["status"] == "ok", r
        assert r["zone"]["id"] == "rafina_port"
        assert r["geometry"]["coordinates"][0] == list(pts[5])


def test_perimeter_and_warnings(tmp_path):
    from shapely.geometry import box, mapping
    data = tmp_path / "pd" / "data"
    data.mkdir(parents=True)
    # A perimeter around the Mati coast only: the ignition point falls outside it.
    per = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": mapping(box(23.97, 38.03, 24.02, 38.06))}]}
    (data / "perimeter.geojson").write_text(json.dumps(per), encoding="utf-8")
    s = Settings(scenario_dir=REPO_ROOT / "scenarios" / "mati_2018", data_dir=data,
                 runtime_dir=tmp_path / "pd" / "runtime")
    with TestClient(create_app(s)) as c:
        sc = c.get("/api/scenario").json()
        assert sc["data"]["perimeter"] is True
        assert any("ignition" in w for w in sc["warnings"])
        assert c.get("/api/perimeter").json()["available"]
