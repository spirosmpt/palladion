from datetime import timedelta

import pytest

from palladion.agents.intake import Hazard, IntakeOutput, IntakeResult, MessageKind, People, Reporter
from palladion.cases.service import CaseError, CaseService
from palladion.engines.registry.registry import Registry
from palladion.pipeline.arbiter import arbitrate
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0

# message id -> (decision, minutes to front, people, hazards, location)
SETUP = {
    "flames": ("DISPATCH_UNIT_CANDIDATE", None, None, [("flames", "βλέπουμε φλόγες")], (23.98, 38.05)),
    "far": ("MONITOR", -20, None, [], (23.98, 38.05)),
    "a": ("DISPATCH_UNIT_CANDIDATE", 30, 3, [], (23.98, 38.05)),
    "b": ("REPORTED_SAFE", 30, 5, [], (23.99, 38.06)),
}
TEXT = {"flames": "Βλέπουμε φλόγες στην αυλή", "far": "Η φωτιά είναι πολύ μακριά, πρέπει να φύγουμε;",
        "a": "Είμαστε 3 στην Κυκλάδων", "b": "Είμαστε 5 και είμαστε καλά, φύγαμε"}


def triage(m):
    decision, mins, people, hazards, (lon, lat) = SETUP[m.id]
    out = IntakeOutput(kind=MessageKind.help_request, reporter=Reporter.self_, language="el",
                       people=People(count=people, quote=f"Είμαστε {people}") if people else None,
                       hazards=[Hazard(kind=k, quote=q) for k, q in hazards], summary_el="-")
    return Processed(m, IntakeResult(output=out), Location("resolved", lon, lat, 100, f"Οδός {m.id}"), mins,
                     Outcome(Decision(decision), 60, ["r"]))


@pytest.fixture
def svc(tmp_path):
    msgs = [Message(id=k, time=T0 + timedelta(minutes=i), channel="sms", text=TEXT[k]) for i, k in enumerate(SETUP)]
    s = CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"))
    s.tick(T0 + timedelta(minutes=10), sync=True)
    return s


def kinds(svc, cid):
    return {(it["kind"], it["strength"]) for it in svc.contradictions(cid)}


def test_the_message_against_the_fire_model(svc):
    assert kinds(svc, "flames") == {("flames_vs_model", "medium")}
    far = svc.contradictions("far")[0]
    assert far["kind"] == "far_vs_model" and far["strength"] == "strong" and far["a"]["quote"] == "πολύ μακριά"
    assert "έχει ήδη περάσει" in far["b"]["quote"]


def test_merged_messages_that_disagree_and_a_person_decides(svc):
    now = T0 + timedelta(minutes=11)
    svc.act("b", "merge", "anna", now, target="a")
    assert kinds(svc, "a") == {("count_linked", "medium"), ("place_linked", "medium"), ("safe_vs_help", "strong")}
    item = next(it for it in svc.contradictions("a") if it["kind"] == "safe_vs_help")
    with pytest.raises(CaseError):
        svc.decide_contradiction("a", item["id"], "maybe", "anna", now)
    after = svc.decide_contradiction("a", item["id"], "second", "anna", now, note="τους πήρε ο γείτονας")
    decided = next(it for it in after if it["id"] == item["id"])["decision"]
    assert decided["verdict"] == "second" and decided["by"] == "anna"
    # Judging which source holds is not acting on the case: the decision stays open.
    assert svc.get("a").status.value == "proposed" and svc.get("a").operator is None


def test_the_arbiter_table():
    out = Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 60, ["r"])
    r = arbitrate(out, [], {"assessment": "likely_false"}, None)
    assert r["rule"] == "likely_false" and r["changed"] and out.decision == Decision.REVIEW_REQUIRED
    out = Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 60, ["r"])
    r = arbitrate(out, ["area_inferred"], {"assessment": "doubtful"}, None)
    assert r["rule"] == "doubtful" and not r["changed"]                      # life safety first
    out = Outcome(Decision.SHELTER_CANDIDATE, 60, ["r"])
    r = arbitrate(out, [], {"assessment": "doubtful"}, {"changed": "EVACUATE_ROUTE_CANDIDATE -> SHELTER_CANDIDATE"})
    assert r["rule"] == "doubtful_safer" and out.decision == Decision.SHELTER_CANDIDATE
    out = Outcome(Decision.MONITOR, 20, ["r"])
    assert arbitrate(out, [], {"assessment": "likely_false"}, None)["rule"] == "none"   # nothing moves anyway


def test_a_long_street_the_front_only_touches_at_one_end(fire):
    """m38 (Δάσους, Αγία Μαρίνα): the point never burns, the nearest part of the street does. The time is kept (safety
    first), the reason says where it was measured, and "far away" against it is a question, not a strong item."""
    from types import SimpleNamespace

    from palladion.pipeline.process import _front

    from .conftest import ll
    street = Location("resolved", *ll(5.3, 0), 500, "Δάσους")            # the conftest front stops at 5 km
    mins, near = _front(street, fire, T0 + timedelta(minutes=120))
    assert mins is not None and mins < 0 and near is not None and 250 <= near <= 500
    area = Location("approximate", *ll(5.3, 0), 500, "Περιοχή")
    assert _front(area, fire, T0)[1] is None                             # an area's centre: as before
    on_point = Location("resolved", *ll(2, 0), 500, "Οδός")
    assert _front(on_point, fire, T0)[1] is None                         # the point itself burns

    from palladion.cases.contradictions import fire_items
    m = Message(id="x", time=T0 + timedelta(minutes=120), channel="sms", text="Η φωτιά είναι πολύ μακριά")
    out = IntakeOutput(kind=MessageKind.help_request, reporter=Reporter.self_, language="el", summary_el="-")
    p = Processed(m, IntakeResult(output=out), street, mins, Outcome(Decision.MONITOR, 20, []), front_nearby_m=near)
    items = fire_items(SimpleNamespace(message=m, processed=p))
    assert items[0]["kind"] == "far_vs_model" and items[0]["strength"] == "medium"
    assert "την ίδια τη θέση δεν τη φτάνει" in items[0]["b"]["quote"]
