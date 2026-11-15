import json
from datetime import timedelta

from palladion.agents.hazard import SYSTEM, HazardContext, facts, needs_review, run_hazard
from palladion.agents.intake import IntakeOutput, IntakeResult
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location

from .conftest import T0

TEXT = "Αιγαίου στο Μάτι, κλειστήκαμε στο μπάνιο, είμαστε 4, ο καπνός μπαίνει μέσα"
INTAKE = IntakeResult(output=IntakeOutput.model_validate({
    "kind": "help_request", "reporter": "self", "language": "el",
    "people": {"count": 4, "quote": "είμαστε 4"},
    "situation": {"kind": "sheltering", "quote": "κλειστήκαμε στο μπάνιο"},
    "places": [{"kind": "street", "name": "Αιγαίου", "quote": "Αιγαίου"}],
    "hazards": [{"kind": "smoke", "quote": "ο καπνός μπαίνει μέσα"}], "summary_el": "-"}))
LOC = Location("resolved", 23.98, 38.05, 200, "Αιγαίου, Μάτι")


class FailingRoads:
    def evacuate(self, *a):
        class R:
            status, min_margin_min, dead_end_edges = "no_safe_route", None, 0
        return R()


def llm_says(answer, tmp_path):
    def fn(system, user, tier):
        assert system.startswith(SYSTEM)
        return json.dumps(answer, ensure_ascii=False)
    return LLM(ScriptedProvider(fn), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def test_facts_are_computed_and_marked_severe():
    ctx = HazardContext(FailingRoads(), [], None, 10)
    fs = facts(INTAKE, LOC, 7, Decision.EVACUATE_ROUTE_CANDIDATE, T0, ctx)
    ids = {f.id: f.severe for f in fs}
    assert ids == {"front": True, "inside": True, "smoke": False, "route": True}
    assert needs_review(Decision.EVACUATE_ROUTE_CANDIDATE, fs)
    calm = facts(INTAKE, LOC, 40, Decision.MONITOR, T0, None)
    assert not needs_review(Decision.MONITOR, calm)


def outcome():
    return Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, 80, ["r"])


def test_safer_move_allowed_when_facts_support_it(tmp_path):
    fs = facts(INTAKE, LOC, 7, Decision.EVACUATE_ROUTE_CANDIDATE, T0, HazardContext(FailingRoads(), [], None, 10))
    ans = {"cautions": [{"fact_id": "inside", "text_el": "Κλεισμένοι μέσα με το μέτωπο σε 7′."},
                        {"fact_id": "wind", "text_el": "Άνεμος 80 km/h."}],              # no such fact: dropped
           "suggest": "SHELTER_CANDIDATE"}
    o, rv = run_hazard(llm_says(ans, tmp_path), TEXT, outcome(), fs)
    assert o.decision == Decision.SHELTER_CANDIDATE and rv.changed and "hazard_changed" in o.flags
    assert len(rv.cautions) == 1 and rv.dropped == ["wind: Άνεμος 80 km/h."]


def test_unsafe_or_unsupported_moves_are_refused(tmp_path):
    fs = facts(INTAKE, LOC, 30, Decision.EVACUATE_ROUTE_CANDIDATE, T0 + timedelta(minutes=1), None)
    o, rv = run_hazard(llm_says({"cautions": [], "suggest": "SHELTER_CANDIDATE"}, tmp_path / "a"), TEXT, outcome(), fs)
    assert o.decision == Decision.EVACUATE_ROUTE_CANDIDATE and rv.refused          # front far, route not failing
    o, rv = run_hazard(llm_says({"cautions": [], "suggest": "REPORTED_SAFE"}, tmp_path / "b"), TEXT, outcome(), fs)
    assert o.decision == Decision.EVACUATE_ROUTE_CANDIDATE and rv.refused          # never towards "safe"


def test_help_on_its_way_is_never_downgraded(tmp_path):
    fs = facts(INTAKE, LOC, -10, Decision.DISPATCH_UNIT_CANDIDATE, T0, None)
    assert not any(f.severe for f in fs if f.id == "front")                  # a passed front is not severe
    sea = Outcome(Decision.SEA_RESCUE_CANDIDATE, 80, ["r"])
    o, rv = run_hazard(llm_says({"cautions": [], "suggest": "REVIEW_REQUIRED"}, tmp_path), TEXT, sea, fs)
    assert o.decision == Decision.SEA_RESCUE_CANDIDATE and "brings help" in rv.refused
    o, rv = run_hazard(llm_says({"cautions": [], "suggest": "KEEP"}, tmp_path / "k"), TEXT, outcome(), fs)
    assert rv.refused is None and o.decision == Decision.EVACUATE_ROUTE_CANDIDATE


def test_a_building_with_smoke_is_not_being_shut_in():
    hotel = IntakeResult(output=IntakeOutput.model_validate({
        "kind": "help_request", "reporter": "self", "language": "en",
        "situation": {"kind": "building", "quote": "at a hotel"},
        "hazards": [{"kind": "smoke", "quote": "smoke everywhere"}], "summary_el": "-"}))
    fs = facts(hotel, LOC, 8, Decision.EVACUATE_ROUTE_CANDIDATE, T0, None)
    assert "inside" not in {f.id for f in fs}
