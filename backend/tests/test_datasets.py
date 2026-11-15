from shapely.geometry import Point, shape

from palladion.datasets import osm


def way(id_, nodes, coords, **tags):
    return {"type": "way", "id": id_, "nodes": nodes, "tags": tags,
            "geometry": [{"lon": x, "lat": y} for x, y in coords]}


def test_roads_split_at_shared_nodes():
    # A T-junction: way 1 runs west-east through node 2; way 2 joins it at node 2.
    els = [way(1, [1, 2, 3], [(0, 0), (1, 0), (2, 0)], highway="residential", name="Α"),
           way(2, [2, 4], [(1, 0), (1, 1)], highway="primary_link", oneway="yes")]
    fc = osm.roads_from_overpass(els)
    ids = sorted(f["properties"]["id"] for f in fc["features"])
    assert ids == ["w1_0", "w1_1", "w2_0"]
    w2 = next(f for f in fc["features"] if f["properties"]["osm_way"] == 2)["properties"]
    assert w2["highway"] == "primary" and w2["oneway"] is True


def test_reverse_oneway_is_flipped():
    fc = osm.roads_from_overpass([way(1, [1, 2], [(0, 0), (1, 0)], highway="service", oneway="-1")])
    assert fc["features"][0]["geometry"]["coordinates"][0] == (1, 0)


def test_land_is_the_inland_side_plus_islands():
    bbox = (0.0, 0.0, 10.0, 10.0)
    coast = way(10, [1, 2, 3], [(6, 11), (6.5, 5), (6, -1)], natural="coastline")
    island = way(11, [5, 6, 7, 8, 5], [(8, 8), (9, 8), (9, 9), (8, 9), (8, 8)], natural="coastline")
    fc = osm.land_from_overpass([coast, island], bbox, inland=(2, 5))
    land = shape(fc["features"][0]["geometry"])
    assert land.contains(Point(2, 5)) and land.contains(Point(8.5, 8.5))
    assert not land.contains(Point(8, 2))


def test_beach_zone_is_anchored_on_land():
    bbox = (23.9, 38.0, 24.1, 38.1)
    coast = way(10, [1, 2], [(24.0, 38.11), (24.0, 37.99)], natural="coastline")
    land = osm.land_from_overpass([coast], bbox, inland=(23.95, 38.05))
    beach = way(20, [1, 2, 3, 4, 1],
                [(23.999, 38.05), (24.002, 38.05), (24.002, 38.051), (23.999, 38.051), (23.999, 38.05)],
                natural="beach", name="Παραλία")
    zones = osm.zones_from_overpass([beach], land)["features"]
    assert len(zones) == 1 and zones[0]["properties"]["kind"] == "beach"
    assert zones[0]["properties"]["capacity_estimate"] > 0
    lon = zones[0]["geometry"]["coordinates"][0]
    assert lon <= 24.0 + 1e-9


def test_buildings_keep_closed_footprints_with_heights():
    sq = [(0, 0), (0.0001, 0), (0.0001, 0.0001), (0, 0.0001), (0, 0)]
    els = [way(1, [1, 2, 3, 4, 1], sq, building="house", **{"building:levels": "2"}),
           way(2, [1, 2, 3, 4, 1], sq, building="yes"),
           way(3, [1, 2, 3], sq[:3], building="yes")]                      # not closed: dropped
    fc = osm.buildings_from_overpass(els)
    heights = {f["properties"]["id"]: f["properties"]["height"] for f in fc["features"]}
    assert heights == {"w1": 6.4, "w2": 6.0}
