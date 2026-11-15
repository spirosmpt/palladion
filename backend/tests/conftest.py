from datetime import datetime, timedelta, timezone

import pytest
from shapely.geometry import box

from palladion.domain.models import Confidence, FireAnchor, FireSpec, LocalizedText, SafeZone, ZoneKind
from palladion.engines.fire.model import AnchoredEllipseFire
from palladion.geo.proj import LocalFrame

TZ = timezone(timedelta(hours=3))
T0 = datetime(2018, 7, 23, 16, 0, tzinfo=TZ)
ORIGIN = (23.90, 38.00)
FRAME = LocalFrame(*ORIGIN)


def ll(x_km: float, y_km: float) -> tuple[float, float]:
    return FRAME.to_lonlat(x_km * 1000, y_km * 1000)


def anchor(id_: str, x_km: float, y_km: float, minutes: float) -> FireAnchor:
    lon, lat = ll(x_km, y_km)
    return FireAnchor(id=id_, label=LocalizedText(el=id_, en=id_), lon=lon, lat=lat,
                      time=T0 + timedelta(minutes=minutes), confidence=Confidence.approximate)


@pytest.fixture
def fire_spec() -> FireSpec:
    """Fire spreading due east: 3 km in the first hour, 2 km more in the next 20 minutes."""
    return FireSpec(ignition=anchor("ign", 0, 0, 0), spread_bearing_deg=90, length_to_breadth=2.0,
                    anchors=[anchor("a1", 3, 0, 60), anchor("a2", 5, 0, 80)], safety_margin_min=10)


@pytest.fixture
def land():
    w, s = ll(-10, -10)
    e, n = ll(20, 10)
    return box(w, s, e, n)


@pytest.fixture
def fire(fire_spec, land) -> AnchoredEllipseFire:
    return AnchoredEllipseFire(fire_spec, land)


Seg = tuple[tuple[float, float], tuple[float, float]]


def grid_roads(xs: list[float], ys: list[float], extra: list[Seg] = ()):
    """GeoJSON grid of roads (km coordinates), one feature per block edge, plus extra segments."""
    feats = []

    def seg(a, b):
        feats.append({"type": "Feature", "properties": {"id": f"e{len(feats)}", "highway": "residential"},
                      "geometry": {"type": "LineString", "coordinates": [ll(*a), ll(*b)]}})

    for y in ys:
        for x1, x2 in zip(xs, xs[1:], strict=False):
            seg((x1, y), (x2, y))
    for x in xs:
        for y1, y2 in zip(ys, ys[1:], strict=False):
            seg((x, y1), (x, y2))
    for a, b in extra:
        seg(a, b)
    return {"type": "FeatureCollection", "features": feats}


def zone(id_: str, x_km: float, y_km: float) -> SafeZone:
    lon, lat = ll(x_km, y_km)
    return SafeZone(id=id_, name=id_, kind=ZoneKind.assembly, lon=lon, lat=lat)
