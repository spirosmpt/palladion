from datetime import timedelta

import pytest

from palladion.agents.brief import check, facts, template, write_brief
from palladion.domain.models import GroundUnitsSpec, LocalizedText, Station, UnitGroup, UnitKind
from palladion.engines.missions import MissionEngine, unit_kind
from palladion.engines.registry.registry import Registry
from palladion.engines.routing.graph import RoadGraph
from palladion.field.service import FieldService
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.missions.service import MissionError, MissionService

from .conftest import T0, grid_roads, ll

XS = [-2, -1, 0, 1, 2, 3, 4, 5, 6]


def road(fire):
    """One road along the fire's path: the front crosses it from west to east."""
    return RoadGraph.from_geojson(grid_roads(XS, [0]), fire)


def spec(engines=1, ambulances=1):
    lon, lat = ll(6, 0)
    base = Station(name="Βάση", lon=lon, lat=lat)
    return GroundUnitsSpec(groups=[
        UnitGroup(id="fire", prefix="ΠΥ", name=LocalizedText(el="Π", en="F"), kind=UnitKind.fire_engine, base=base,
                  count=engines, ready_min=0, on_scene_min=10),
        UnitGroup(id="ekab", prefix="ΕΚΑΒ", name=LocalizedText(el="Ε", en="A"), kind=UnitKind.ambulance, base=base,
                  count=ambulances, ready_min=0, on_scene_min=10)])


# -- route ----------------------------------------------------------------------------------------------
def test_drive_before_the_front_and_after_it_burned_out_but_not_through_it(fire):
    g = road(fire)
    early = g.drive_to(*ll(6, 0), *ll(1, 0), T0, 10)
    assert early.status == "ok" and not early.warnings
    burning = g.drive_to(*ll(6, 0), *ll(1, 0), T0 + timedelta(minutes=40), 10)
    assert burning.status == "no_safe_route"
    late = g.drive_to(*ll(6, 0), *ll(1, 0), T0 + timedelta(minutes=120), 10)
    assert late.status == "ok" and "περάσει" in late.warnings[0]


def test_the_destination_itself_is_never_refused(fire):
    # The place is burning now: that is why the unit goes. Only the roads on the way are checked.
    g = road(fire)
    r = g.drive_to(*ll(6, 0), *ll(4, 0), T0 + timedelta(minutes=55), 10)
    assert r.status == "ok"


# -- plan -----------------------------------------------------------------------------------------------
def test_unit_kind_from_what_people_need():
    assert unit_kind(["burns"], [])[0] == UnitKind.ambulance
    assert unit_kind([], ["medical"])[0] == UnitKind.ambulance
    assert unit_kind(["elderly", "child"], [])[0] == UnitKind.fire_engine


def test_veto_with_the_sea_as_alternative_by_the_shore(fire):
    eng = MissionEngine(spec(), road(fire), fire, 10, near_shore=lambda lon, lat: True)
    p = eng.plan("m1", *ll(1, 0), [], [], T0 + timedelta(minutes=40), set())
    assert p.unit is None and p.veto and p.alternatives == ["sea", "review"]


def test_busy_unit_is_not_planned_and_the_other_kind_is_offered(fire):
    eng = MissionEngine(spec(engines=1, ambulances=1), road(fire), fire, 10)
    p = eng.plan("m1", *ll(3, 0), [], [], T0, {"ΠΥ-01"})
    # The only fire engine is busy (its crew has not said it is free): the ambulance goes, and the operator
    # is told why.
    assert p.unit is not None and p.unit.id == "ΕΚΑΒ-01" and p.other_kind
    assert "ασθενοφόρο" in p.warnings[0]


def test_front_before_the_unit_is_one_fact_not_also_a_warning(fire):
    eng = MissionEngine(spec(), road(fire), fire, 10)
    p = eng.plan("m1", *ll(1, 0), [], [], T0 + timedelta(minutes=10), set())
    assert p.unit is not None and p.front_min is not None and p.front_min < 0
    # Said once, from front_min, never repeated as a warning.
    assert not any("μέτωπο φτάνει" in w for w in p.warnings)


# -- brief ----------------------------------------------------------------------------------------------
CASE = {"lon": 0, "lat": 0, "address": "Κυκλάδων, Μάτι", "people": 4, "vulnerable": ["elderly"], "asks_for": [],
        "situation": "δεν μπορούμε να βγούμε", "message": "Κυκλάδων 12 στο Μάτι, 4 άτομα, η γιαγιά δεν περπατά",
        "decision": "DISPATCH_UNIT_CANDIDATE", "open": True}


def scripted(fn, tmp_path):
    return LLM(ScriptedProvider(fn), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def plan_for(fire):
    eng = MissionEngine(spec(), road(fire), fire, 10)
    return eng.plan("m1", *ll(3, 0), [], [], T0, set())


def test_template_brief_passes_its_own_check(fire):
    p = plan_for(fire)
    f = facts(CASE, p, T0)
    assert check(template(f), f, CASE["message"]) == []


def test_brief_with_an_invented_number_is_rejected_then_revised(fire, tmp_path):
    p = plan_for(fire)
    answers = iter(['{"text": "Κυκλάδων, Μάτι: 7 άτομα εγκλωβισμένα."}',
                    '{"text": "Κυκλάδων 12, Μάτι: 4 άτομα, μια ηλικιωμένη δεν περπατά."}'])
    llm = scripted(lambda s, u, t: next(answers), tmp_path)
    b = write_brief(llm, CASE, p, T0)
    assert b.source == "revised" and "4 άτομα" in b.text and len(b.calls) == 2


def test_brief_without_the_address_falls_back_to_the_template(fire, tmp_path):
    p = plan_for(fire)
    llm = scripted(lambda s, u, t: '{"text": "Τέσσερα άτομα σε κίνδυνο."}', tmp_path)
    b = write_brief(llm, CASE, p, T0)
    assert b.source == "template" and b.text.startswith("Προς Κυκλάδων") and b.problems


# -- service --------------------------------------------------------------------------------------------
@pytest.fixture
def service(fire, tmp_path):
    reg = Registry(tmp_path / "events.jsonl")
    cases = {"m1": {**CASE, "lon": ll(3, 0)[0], "lat": ll(3, 0)[1]},
             "m2": {**CASE, "lon": ll(3, 0)[0], "lat": ll(3, 0)[1], "decision": "SHELTER_CANDIDATE"},
             "m3": {**CASE, "lon": ll(3, 0)[0], "lat": ll(3, 0)[1], "open": False}}
    eng = MissionEngine(spec(), road(fire), fire, 10)
    return MissionService(eng, reg, None, cases.get, FieldService(reg, None)), eng, reg, cases


def test_dispatch_is_a_human_event_and_needs_a_dispatch_decision(service):
    svc, _, reg, _ = service
    with pytest.raises(MissionError):
        svc.dispatch("m2", "anna", T0)
    m = svc.dispatch("m1", "anna", T0)
    ev = list(reg.events("unit:" + m.unit))[-1]
    assert ev.type == "unit.dispatched" and ev.actor.kind.value == "human" and ev.data["case"] == "m1"
    assert m.brief_source == "template"
    # Estimates never move a unit: long after the estimated arrival it is still only "assigned".
    assert svc.field.mission_phase(m.id, m.arrive + timedelta(minutes=5)) == "assigned"
    # The same engine is now busy: the next plan for the case takes the ambulance.
    assert svc.plan("m1", T0)["plan"]["unit"] == "ΕΚΑΒ-01"


def test_crew_reports_move_the_unit_and_free_it(service):
    svc, _, _, _ = service
    m = svc.dispatch("m1", "anna", T0)
    f = svc.field
    f.report(m.unit, "Φύγαμε από τη βάση.", T0 + timedelta(minutes=1))
    assert f.mission_phase(m.id, T0 + timedelta(minutes=2)) == "en_route"
    f.report(m.unit, "Φτάσαμε.", T0 + timedelta(minutes=9))
    assert f.mission_phase(m.id, T0 + timedelta(minutes=9)) == "on_scene"
    f.report(m.unit, "", T0 + timedelta(minutes=30), source="operator", button="available")
    assert f.mission_phase(m.id, T0 + timedelta(minutes=30)) == "done"
    assert svc.plan("m1", T0 + timedelta(minutes=30))["plan"]["unit"] == "ΠΥ-01"


def test_a_breakdown_holds_the_unit_until_it_is_reported_available(service):
    svc, _, _, _ = service
    m = svc.dispatch("m1", "anna", T0)
    f = svc.field
    f.report(m.unit, "Βλάβη στη μηχανή, ακινητοποιηθήκαμε.", T0 + timedelta(minutes=3))
    f.report(m.unit, "Φτάσαμε.", T0 + timedelta(minutes=5))           # cannot be: a breakdown is not cleared
    assert f.state(m.unit, T0 + timedelta(minutes=6))["state"] == "out_of_service"
    f.report(m.unit, "Διαθέσιμοι στη βάση.", T0 + timedelta(minutes=50))
    assert f.state(m.unit, T0 + timedelta(minutes=50))["state"] == "available"


def test_recall_and_restore_rebuild_the_desk(service, fire):
    svc, eng, reg, cases = service
    m = svc.dispatch("m1", "anna", T0)
    half = m.depart + (m.arrive - m.depart) / 2
    svc.field.report(m.unit, "Φύγαμε.", T0 + timedelta(minutes=1))
    svc.recall(m.id, "anna", half)
    assert svc.field.mission_phase(m.id, half) == "recalled"
    assert svc.field.state(m.unit, half)["state"] == "unknown"           # until the crew says where it is
    again = MissionService(eng, reg, None, cases.get, FieldService(reg, None))
    assert len(again.missions) == 1 and again.missions[0].recalled_at == half
    assert again.missions[0].brief == m.brief and len(again.field.reports) == 1


def test_a_slow_land_unit_by_the_shore_also_suggests_a_boat(fire):
    eng = MissionEngine(spec(), road(fire), fire, 10, near_shore=lambda lon, lat: True)
    p = eng.plan("m1", *ll(-1, 0), [], [], T0 + timedelta(minutes=125), set())   # 21' along the burned road
    assert p.unit is not None and p.alternatives == ["sea"] and "σκάφος" in p.warnings[-1]


def test_numbers_written_as_words_are_checked_too(fire):
    p = plan_for(fire)
    f = facts(CASE, p, T0)
    assert check("Κυκλάδων, Μάτι: επτά άτομα.", f, CASE["message"]) == ["numbers not in the facts or the message: 7"]
    # "μια" is also the article: it is never read as a number.
    assert check("Κυκλάδων, Μάτι: τέσσερα άτομα, μια ηλικιωμένη δεν περπατά.", f, CASE["message"]) == []


def test_a_closed_case_gets_no_new_unit(service):
    svc, _, _, _ = service
    assert svc.plan("m3", T0)["can_send"] is False
    with pytest.raises(MissionError):
        svc.dispatch("m3", "anna", T0)


def test_brief_in_the_first_person_of_the_sender_is_rejected(fire):
    p = plan_for(fire)
    f = facts(CASE, p, T0)
    assert check("Κυκλάδων, Μάτι. Είμαστε στο σπίτι, 4 άτομα.", f, CASE["message"])
    assert check("Κυκλάδων, Μάτι: λένε «είμαστε στο σπίτι», 4 άτομα.", f, CASE["message"]) == []


def test_brief_says_who_is_vulnerable_in_the_senders_words(fire):
    p = plan_for(fire)
    f = facts({**CASE, "vulnerable_quotes": [["respiratory", "ο πατέρας μου έχει άσθμα"]]}, p, T0)
    assert f["vulnerable_said"] == ["αναπνευστικό πρόβλημα: «ο πατέρας μου έχει άσθμα»"]
    assert "άσθμα" not in template(f)                   # the template keeps to the labels


def test_another_unit_than_the_proposal_needs_a_reason(service):
    svc, _, reg, _ = service
    with pytest.raises(MissionError):
        svc.dispatch("m1", "anna", T0, unit="ΕΚΑΒ-01")                       # the proposal is the fire engine
    m = svc.dispatch("m1", "anna", T0, unit="ΕΚΑΒ-01", reason="Ο ασθενής έχει εγκαύματα κατά το τηλέφωνο.")
    assert m.override_reason and list(reg.events("unit:ΕΚΑΒ-01"))[-1].data["rank"] == 2
    assert [o["unit"] for o in svc.plan("m1", T0)["plan"]["options"]] == ["ΠΥ-01"]


def test_the_checklist_is_complete_before_a_unit_leaves(service):
    from palladion.missions.service import CHECKLIST
    svc, _, reg, _ = service
    with pytest.raises(MissionError, match="μίλησα με το πλήρωμα"):
        svc.dispatch("m1", "anna", T0, checklist=["place", "route", "warnings"])
    assert not svc.missions
    m = svc.dispatch("m1", "anna", T0, checklist=list(CHECKLIST))
    assert list(reg.events("unit:" + m.unit))[-1].data["checklist"] == sorted(CHECKLIST)
