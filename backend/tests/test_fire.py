from datetime import timedelta

import pytest
from shapely.geometry import Point, box

from palladion.config import REPO_ROOT
from palladion.engines.fire.model import AnchoredEllipseFire, InconsistentAnchors
from palladion.scenarios.loader import load_scenario

from .conftest import T0, anchor, ll


def test_front_passes_every_anchor_at_its_time(fire, fire_spec):
    for a in [fire_spec.ignition, *fire_spec.anchors]:
        t = fire.arrival_time(a.lon, a.lat)
        assert t is not None
        assert abs((t - a.time).total_seconds()) < 1.0


def test_arrival_increases_downwind(fire):
    times = [fire.arrival_time(*ll(x, 0)) for x in (0.5, 1.5, 2.5, 3.5, 4.5)]
    assert all(t is not None for t in times)
    assert times == sorted(times)


def test_upwind_and_flanks_burn_later_than_head(fire):
    head = fire.arrival_time(*ll(1, 0))
    flank = fire.arrival_time(*ll(0, 0.5))
    back = fire.arrival_time(*ll(-0.3, 0))
    assert head is not None
    assert flank is not None and back is not None
    assert head < flank < back


def test_beyond_final_front_never_burns(fire):
    assert fire.arrival_time(*ll(8, 0)) is None
    assert fire.arrival_time(*ll(0, 5)) is None


def test_outside_land_never_burns(fire_spec):
    w, s = ll(-10, -10)
    e, n = ll(2, 10)                      # coast at x = 2 km
    f = AnchoredEllipseFire(fire_spec, box(w, s, e, n))
    assert f.arrival_time(*ll(1, 0)) is not None
    assert f.arrival_time(*ll(3, 0)) is None


def test_front_grows_and_contains_anchor_after_its_time(fire, fire_spec):
    a1 = fire_spec.anchors[0]
    assert fire.front(T0 - timedelta(minutes=1)).is_empty
    before = fire.front(a1.time - timedelta(minutes=2))
    after = fire.front(a1.time + timedelta(minutes=2))
    assert after.area > before.area
    assert not before.contains(Point(a1.lon, a1.lat))
    assert after.contains(Point(a1.lon, a1.lat))


def test_front_stops_after_last_anchor(fire, fire_spec):
    last = fire_spec.anchors[-1].time
    assert fire.front(last).area == pytest.approx(fire.front(last + timedelta(hours=2)).area)


def test_backwards_anchors_are_rejected(fire_spec, land):
    bad = fire_spec.model_copy(update={"anchors": [anchor("a1", 3, 0, 60), anchor("a2", 2, 0, 80)]})
    with pytest.raises(InconsistentAnchors):
        AnchoredEllipseFire(bad, land)


def test_mati_scenario_is_consistent():
    data = load_scenario(REPO_ROOT / "scenarios" / "mati_2018", REPO_ROOT / "nonexistent")
    f = AnchoredEllipseFire(data.spec.fire, data.land)
    for a in [data.spec.fire.ignition, *data.spec.fire.anchors]:
        t = f.arrival_time(a.lon, a.lat)
        assert t is not None, f"anchor {a.id} must be on land"
        assert abs((t - a.time).total_seconds()) < 1.0
    # The Rafina port (south of the fire) is outside the reconstructed burned area.
    port = data.spec.fallback_zones[0]
    assert f.arrival_time(port.lon, port.lat) is None


# -- constrained to an observed perimeter ---------------------------------------------------------
def perimeter_box(x0, y0, x1, y1):
    from shapely.geometry import Polygon
    return Polygon([ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1)])


def test_perimeter_keeps_anchor_times_and_fills_the_flanks(fire_spec, land):
    f = AnchoredEllipseFire(fire_spec, land, perimeter_box(-0.2, -1.5, 5.2, 1.5))
    for a in [fire_spec.ignition, *fire_spec.anchors]:
        assert abs((f.arrival_time(a.lon, a.lat) - a.time).total_seconds()) < 1.0
    last = fire_spec.anchors[-1].time
    flank = f.arrival_time(*ll(0.5, 1.3))             # outside the final ellipse, inside the perimeter
    assert flank is not None
    assert last < flank <= last + timedelta(minutes=fire_spec.post_head_spread_min)
    assert f.arrival_time(*ll(-0.3, 0)) is None       # behind the perimeter edge
    assert f.arrival_time(*ll(2, 2.0)) is None        # beside it
    assert f.final_time == last + timedelta(minutes=fire_spec.post_head_spread_min)
    # By the end the whole perimeter has burned.
    assert f.front(f.final_time).area == pytest.approx(f.land.area, rel=1e-3)


def test_label_point_sits_on_the_moving_front(fire_spec, land):
    f = AnchoredEllipseFire(fire_spec, land, perimeter_box(-0.2, -1.5, 5.2, 1.5))
    t = fire_spec.anchors[0].time
    lon, lat = f.label_point(t, f.front(t))
    arr = f.arrival_time(lon, lat)
    assert arr is not None and abs((arr - t).total_seconds()) < 120


def test_front_edge_excludes_perimeter_and_coast(fire_spec, land):
    f = AnchoredEllipseFire(fire_spec, land, perimeter_box(-0.2, -1.5, 5.2, 1.5))
    t = fire_spec.anchors[-1].time + timedelta(minutes=20)    # flanks are burning along the perimeter
    geom = f.front(t)
    edge = f.front_edge(geom)
    assert not edge.is_empty
    assert edge.length < geom.boundary.length * 0.8
    # No part of the edge lies on the perimeter.
    assert edge.intersection(f.land.boundary.buffer(1e-6)).length < 1e-9
