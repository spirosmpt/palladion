import json
from datetime import timedelta

import numpy as np
import pytest
from shapely.geometry import box

from palladion.agents.alert_writer import AlertText, check, latin, mentions, template, write_alert
from palladion.alerts.service import AlertError, AlertService, AlertStatus
from palladion.datasets.dem import Dem, tile_range
from palladion.engines.buildings import BuildingLayer
from palladion.engines.coast import CoastAccess, Shore
from palladion.engines.registry.registry import Registry
from palladion.engines.routing.graph import RoadGraph
from palladion.engines.warnings import Action, WarningEngine, coastline, settlement_cells
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider

from .conftest import T0, grid_roads, ll, zone
from .test_buildings import square

END = T0 + timedelta(minutes=80)


def frame():
    w, s = ll(-10, -10)
    e, n = ll(20, 10)
    return (w, s, e, n)


def town(name, x, y):
    return (name, *ll(x, y))


def buildings(fire, xs, y=0.0):
    fc = {"type": "FeatureCollection", "features": [square(x, y, f"b{i}") for i, x in enumerate(xs)]}
    return BuildingLayer(fc, fire)


def make_dem(height, bbox):
    """A synthetic elevation grid: height(lon, lat) in metres, at the real tile resolution."""
    xs, ys = tile_range(bbox, 13)
    grid = Dem(np.zeros((len(ys) * 256, len(xs) * 256)), xs.start, ys.start, 13)
    h, w = grid.elev.shape
    n = 2 ** 13
    px = (xs.start + (np.arange(w) + 0.5) / 256) / n * 360 - 180
    py = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ys.start + (np.arange(h) + 0.5) / 256) / n))))
    lon, lat = np.meshgrid(px, py)
    grid.elev = height(lon, lat).astype(np.float32)
    return grid


def access(land, dem=None, cliffs=None, beaches=()):
    shore = coastline(land, frame())
    return None if shore.is_empty else CoastAccess(shore, land, dem, cliffs, list(beaches))


def engine(fire, land, b, roads=None, zones=(), coast=None):
    bbox = frame()
    sts = settlement_cells([town("Άλφα", 2, 0), town("Βήτα", 4.5, 0), town("Γάμμα", 0, 8)], land, bbox)
    return WarningEngine(fire, sts, b, roads, list(zones), coast if coast is not None else access(land), 10, END)


def test_settlement_cells_cover_the_land_without_overlap(land):
    sts = settlement_cells([town("Άλφα", 2, 0), town("Βήτα", 4.5, 0), town("Γάμμα", 0, 8)], land, frame())
    assert [s.name for s in sts] == ["Άλφα", "Βήτα", "Γάμμα"] or {s.name for s in sts} == {"Άλφα", "Βήτα", "Γάμμα"}
    total = sum(s.cell.area for s in sts)
    assert total == pytest.approx(land.intersection(box(*frame())).area, rel=1e-6)


def test_bands_and_instruction_without_roads(fire, land):
    # Fire due east: 3 km at 60', 5 km at 80'. Buildings in Βήτα at 3.3-3.9 km and 4.35-4.85 km.
    b = buildings(fire, [3.3, 3.45, 3.6, 3.75, 3.9, 4.35, 4.5, 4.65, 4.75, 4.85])
    now = T0 + timedelta(minutes=58)
    threats = engine(fire, land, b).assess(now)
    bands = {(t.settlement, t.band): t.buildings for t in threats}
    assert bands[("Βήτα", (0, 15))] == 5 and bands[("Βήτα", (15, 30))] == 5
    # No roads: never "leave by car". The land box has no shore inside the frame, so: shelter.
    assert {t.instruction.action for t in threats} == {Action.shelter}

    # Too few buildings: no warning.
    few = buildings(fire, [3.5, 3.7])
    assert engine(fire, land, few).assess(now) == []


def shore_land():
    w, s = ll(-10, -10)
    e, n = ll(5.3, 10)               # the sea starts at x = 5.3 km
    return box(w, s, e, n)


def test_walk_to_a_beach_only_where_people_can_reach_the_water(fire):
    land = shore_land()
    b = buildings(fire, [4.5, 4.55, 4.6, 4.65, 4.7, 4.75])
    now = T0 + timedelta(minutes=66)
    # Nobody has checked the shore (no elevation, no mapped beach): never "go to the sea".
    t = engine(fire, land, b).assess(now)
    assert t and t[0].instruction.action == Action.shelter

    # A mapped beach in front of the houses: walk there, by name.
    beach = box(*ll(5.25, -0.3), *ll(5.3, 0.3))
    t = engine(fire, land, b, coast=access(land, beaches=[("Λιμανάκι", beach)])).assess(now)
    ins = t[0].instruction
    assert ins.action == Action.go_to_coast and ins.coast_name == "Λιμανάκι" and ins.coast_m < 800
    assert "Λιμανάκι" in template(t[0]).el and check(template(t[0]), t[0], []) == []


def test_elevation_finds_cliffs_and_low_shores(fire):
    land = shore_land()
    x_sea = ll(5.3, 0)[0]
    # The ground rises 20 m within 40 m of the water north of y = 0 (a bluff), stays flat to the south.
    k = 111_320 * np.cos(np.radians(38.0))

    def height(lon, lat):
        inland_m = (x_sea - lon) * k
        bluff = np.clip(inland_m, 0, 40) / 40 * 20
        return np.where(lat > 38.0, bluff, np.clip(inland_m, 0, 400) / 400 * 3)

    coast = access(land, dem=make_dem(height, frame()))
    north, south = ll(5.2, 1.0), ll(5.2, -1.0)
    assert coast.nearest_shore(*north)[0] == Shore.cliff
    assert coast.nearest_shore(*south)[0] == Shore.gentle
    a = coast.nearest_access(*north)
    assert a is not None and a.lat < 38.0             # the way into the water is south of the bluff
    assert coast.near_cliff(*north) and not coast.near_cliff(*ll(5.2, -2.0))
    kinds = {f["properties"]["kind"] for f in coast.to_geojson()["features"]}
    assert {"cliff", "gentle"} <= kinds

    # The grid is coarser than the shore: a bluff often shows as high ground at the water line itself.
    flat_top = access(land, dem=make_dem(lambda lon, lat: np.where(lat > 38.0, 12.0, 1.0), frame()))
    assert flat_top.nearest_shore(*north)[0] == Shore.cliff
    assert flat_top.nearest_shore(*south)[0] == Shore.gentle


def test_every_sample_must_get_out_to_evacuate(fire, land):
    roads = RoadGraph.from_geojson(grid_roads([3, 4, 5, 6], [0, 3, 6, 9]), fire)
    b = buildings(fire, [4.2, 4.35, 4.5, 4.65, 4.8])
    far = zone("Z", 6, 9)                    # north-east, never near the burned area
    near = zone("N", 5.5, 0.5)               # right next to the fire's path: not a refuge
    eng = engine(fire, land, b, roads, [far, near])
    assert [z.id for z in eng.zones] == ["Z"]
    t = eng.assess(T0 + timedelta(minutes=50))
    ins = t[0].instruction
    assert ins.action == Action.evacuate and ins.routed == ins.sampled
    assert ins.destination in {"Βήτα", "Γάμμα", "Άλφα"}
    late = eng.assess(T0 + timedelta(minutes=69))
    assert all(x.instruction.action != Action.evacuate for x in late if x.band == (0, 15))


def test_greek_names_are_found_in_any_case_or_script():
    assert mentions("Φύγετε προς τη Ραφήνα μέσω της Λεωφόρου Μαραθώνος", "Λεωφόρος Μαραθώνος")
    assert mentions("Leave towards Rafina via Marathonos avenue", "Μαραθώνος")
    assert mentions("112: Wildfire in Mati.", "Μάτι")
    assert not mentions("στο Μάτι", "Νέα Μάκρη")
    assert latin("Νέος Βουτζάς") == "Neos Voutzas" and latin("Ζούμπερι") == "Zouberi"


def threat_for_text(fire, land):
    b = buildings(fire, [3.3, 3.45, 3.6, 3.75, 3.9])
    return engine(fire, land, b).assess(T0 + timedelta(minutes=58))[0]


def test_template_passes_and_checks_catch_changes(fire, land):
    t = threat_for_text(fire, land)
    others = ["Άλφα", "Βήτα", "Γάμμα"]
    assert check(template(t), t, others) == []
    bad = AlertText(el="112: Πυρκαγιά στη Βήτα. Πηγαίνετε στη Γάμμα με το αυτοκίνητο.",
                    en="112: Fire in Vita. Drive to Gamma.")
    problems = " ".join(check(bad, t, others))
    assert "Γάμμα" in problems and "shelter" in problems


def scripted(answers, tmp_path):
    it = iter(answers)
    return LLM(ScriptedProvider(lambda s, u, tier: json.dumps(next(it), ensure_ascii=False)),
               ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def test_writer_retries_once_then_falls_back_to_the_template(fire, land, tmp_path):
    t = threat_for_text(fire, land)
    good = {"el": "112: Πυρκαγιά στη Βήτα σε λιγότερο από 15 λεπτά. Μη χρησιμοποιείτε αυτοκίνητο. "
                  "Μείνετε μέσα σε κτίριο από τσιμέντο.",
            "en": "112: Wildfire in Vita in less than 15 minutes. Do not use a car. Stay inside a concrete building."}
    wrong = {"el": "Φύγετε!", "en": "Run!"}
    w = write_alert(scripted([wrong, good], tmp_path / "a"), t, ["Βήτα"])
    assert w.source == "agent" and w.text.el == good["el"]
    w = write_alert(scripted([wrong, wrong], tmp_path / "b"), t, ["Βήτα"])
    assert w.source == "template" and w.problems
    assert write_alert(None, t, []).source == "template"


def test_desk_proposes_sends_only_by_a_person_and_survives_restart(fire, land, tmp_path):
    b = buildings(fire, [3.3, 3.45, 3.6, 3.75, 3.9, 4.35, 4.5, 4.65, 4.75, 4.85])
    eng = engine(fire, land, b)
    reg = Registry(tmp_path / "ev.jsonl")
    desk = AlertService(eng, None, reg)
    now = T0 + timedelta(minutes=58)
    desk.tick(now, sync=True)
    alerts = desk.visible()
    assert len(alerts) == 2 and all(a.status == AlertStatus.proposed for a in alerts)
    first = alerts[0]
    with pytest.raises(AlertError):
        desk.act(first.id, "edit", "op", now, AlertText(el="112: κάτι άλλο", en="112: something"))
    desk.act(first.id, "send", "op", now)
    types = [e.type for e in reg.events(f"alert:{first.id}")]
    assert types == ["alert.proposed", "alert.approved", "alert.sent"]
    desk.tick(now + timedelta(minutes=1), sync=True)            # within 5': no new assessment
    assert len(desk.visible()) == 2

    again = AlertService(eng, None, Registry(tmp_path / "ev.jsonl"))
    again.tick(now, sync=True)
    assert again.get(first.id).status == AlertStatus.sent


def test_operator_request(fire, land, tmp_path):
    b = buildings(fire, [3.3, 3.5])
    desk = AlertService(engine(fire, land, b), None, Registry(tmp_path / "ev.jsonl"))
    now = T0 + timedelta(minutes=50)
    desk.tick(now, sync=True)
    assert desk.visible() == []                                   # 2 buildings: below the automatic threshold
    a = desk.request(*ll(3.4, 0), now, "op", sync=True)
    assert a.origin == "operator" and a.threat.settlement == "Βήτα" and a.status == AlertStatus.proposed
    with pytest.raises(AlertError):
        desk.request(*ll(0, 8), now, "op", sync=True)              # Γάμμα: the fire never gets there


def test_capital_letters_are_rejected_except_112_and_given_names(fire, land):
    t = threat_for_text(fire, land)
    base = template(t)
    shout = AlertText(el=base.el.replace("Μη χρησιμοποιείτε", "ΜΗΝ χρησιμοποιείτε"), en=base.en)
    assert any("capitals" in p for p in check(shout, t, []))
    assert check(base, t, []) == []


def test_unnamed_beach_is_described_by_the_street_that_ends_there(fire):
    land = shore_land()
    b = buildings(fire, [4.5, 4.55, 4.6, 4.65, 4.7, 4.75])
    now = T0 + timedelta(minutes=66)
    beach = box(*ll(5.25, -0.3), *ll(5.3, 0.3))
    coast = access(land, beaches=[("", beach)])
    street = {"type": "Feature", "properties": {"id": "s", "highway": "residential", "name": "Θάλασσας"},
              "geometry": {"type": "LineString", "coordinates": [ll(4.4, 0), ll(5.28, 0)]}}
    along = {"type": "Feature", "properties": {"id": "p", "highway": "residential", "name": "Παραλιακή"},
             "geometry": {"type": "LineString", "coordinates": [ll(5.25, -1), ll(5.25, 1)]}}
    roads = RoadGraph.from_geojson({"type": "FeatureCollection", "features": [street, along]}, fire)
    t = engine(fire, land, b, roads=roads, coast=coast).assess(now)[0]
    ins = t.instruction
    assert ins.action == Action.go_to_coast and ins.coast_name is None and ins.coast_road == "Θάλασσας"
    text = template(t)
    assert "οδού Θάλασσας" in text.el and check(text, t, []) == []

    # No name and no street ending there: the beach cannot be described, so it is not proposed.
    t = engine(fire, land, b, coast=coast).assess(now)[0]
    assert t.instruction.action == Action.shelter
