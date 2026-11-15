from datetime import timedelta

from palladion.engines.buildings import BuildingLayer

from .conftest import T0, ll


def square(x_km, y_km, id_):
    d = 0.01
    pts = [ll(x_km - d, y_km - d), ll(x_km + d, y_km - d), ll(x_km + d, y_km + d), ll(x_km - d, y_km + d)]
    return {"type": "Feature", "properties": {"id": id_, "height": 6.0},
            "geometry": {"type": "Polygon", "coordinates": [[*pts, pts[0]]]}}


def test_buildings_are_reached_in_order(fire):
    fc = {"type": "FeatureCollection",
          "features": [square(1, 0, "a"), square(3, 0, "b"), square(4.5, 0, "c"), square(0, 6, "never")]}
    b = BuildingLayer(fc, fire)
    assert len(b) == 4 and b.reached_by(10**9) == 3
    assert b.reached_by(0) == 0
    assert b.reached_by((timedelta(minutes=61)).total_seconds()) == 2     # anchor a1 is 3 km at 60 min
    assert b.reached_by(10**6) == 3
    props = {f["properties"]["id"]: f["properties"]["fire_epoch"] for f in b.to_geojson()["features"]}
    assert props["never"] == -1
    assert props["a"] < props["b"] < props["c"]
    assert abs(props["b"] - (T0 + timedelta(minutes=60)).timestamp()) < 30
