from datetime import timedelta

from palladion.engines.routing.graph import RoadGraph

from .conftest import T0, grid_roads, ll, zone

XS = [-2, -1, 0, 1, 2, 3, 4, 5, 6]
YS = [-2, 0, 2]


def graph(fire, extra=()):
    return RoadGraph.from_geojson(grid_roads(XS, YS, list(extra)), fire)


def test_dead_end_branches_are_marked(fire):
    g = graph(fire, extra=[((6, 0), (7, 0)), ((7, 0), (7.5, 0.5))])
    by_end = {(round(g.nodes[e.u][0], 5), round(g.nodes[e.v][0], 5)): e for e in g.edges}
    spur = [e for e in g.edges if e.id in ("e" + str(len(g.edges) - 2), "e" + str(len(g.edges) - 1))]
    assert len(spur) == 2 and all(e.dead_end for e in spur)
    assert sum(e.dead_end for e in g.edges) == 2
    assert by_end  # graph built


def test_route_avoids_the_fire_with_margin(fire):
    g = graph(fire)
    r = g.evacuate(*ll(2, 0), T0 + timedelta(minutes=20), [zone("west", -2, 2)], margin_min=10)
    assert r.status == "ok"
    assert r.zone.id == "west"
    assert r.min_margin_min is not None and r.min_margin_min >= 10
    # The straight road west along y=0 is already burning, so the route must leave via the north.
    assert any(abs(lat - ll(0, 2)[1]) < 1e-6 for _, lat in r.coords)


def test_no_safe_route_when_every_exit_is_too_late(fire):
    g = graph(fire)
    r = g.evacuate(*ll(2, 0), T0 + timedelta(minutes=39), [zone("west", -2, 2)], margin_min=10)
    assert r.status == "no_safe_route"
    assert r.warnings


def test_origin_already_burned(fire):
    g = graph(fire)
    r = g.evacuate(*ll(1, 0), T0 + timedelta(minutes=40), [zone("west", -2, 2)], margin_min=10)
    assert r.status == "origin_burned"


def test_road_status_over_time(fire):
    g = graph(fire)
    e = next(e for e in g.edges if {g.nodes[e.u], g.nodes[e.v]} == {ll(2, 0), ll(3, 0)})
    assert g.status(e, T0).value == "open"
    assert g.status(e, T0 + timedelta(minutes=25)).value == "threatened"
    assert g.status(e, T0 + timedelta(minutes=45)).value == "burned"


def test_spur_that_ends_at_the_refuge_is_not_a_trap(fire):
    g = graph(fire, extra=[((6, -2), (7, -3))])
    r = g.evacuate(*ll(4, 2), T0, [zone("port", 7, -3)], margin_min=10)
    assert r.status == "ok" and r.dead_end_edges == 0
