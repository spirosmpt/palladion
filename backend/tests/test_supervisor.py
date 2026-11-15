from datetime import timedelta

import pytest

from palladion.engines.registry.registry import Forbidden, Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.supervisor import detectors as D
from palladion.supervisor.agent import SupervisorOutput, check
from palladion.supervisor.service import SupervisorError, SupervisorService

from .conftest import T0


def case(id_, minutes, **kw):
    base = {"id": id_, "time": T0 + timedelta(minutes=minutes), "status": "proposed",
            "decision": "EVACUATE_ROUTE_CANDIDATE", "open": True, "open_or_handled": True, "minutes_left": 30.0,
            "label": "Κυκλάδων, Μάτι", "people": 2, "point": (23.98, 38.05), "acted_at": None, "callback_sent": None,
            "callback_reply": None, "callback_cancelled": False, "missions": [], "active_missions": []}
    return {**base, **kw}


def snap(cases=(), missions=(), units=()):
    return {"cases": list(cases), "missions": list(missions), "units": list(units)}


NOW = T0 + timedelta(minutes=60)


# -- detectors ------------------------------------------------------------------------------------------
def test_front_near_without_a_decision():
    s = snap([case("m1", 50, minutes_left=4.0), case("m2", 50, minutes_left=-3.0),
              case("m3", 50, minutes_left=30.0), case("m4", 50, minutes_left=2.0, status="approved")])
    [f] = D.front_no_decision(s, NOW, D.Config())
    assert f["case_ids"] == ["m1", "m2"] and f["numbers"]["passed"] == 1 and "έχει φτάσει" in f["text_el"]


def test_unit_silent_and_idle_and_down():
    m = {"id": "m1-1", "case": "m1", "unit": "ΠΥ-01", "phase": "en_route", "since": T0,
         "arrive": NOW - timedelta(minutes=12), "label_el": "καθ' οδόν", "on_scene_min": 20.0}
    assert D.unit_silent(snap(missions=[m]), NOW, D.Config())[0]["numbers"]["late_min"] == 12
    assert D.unit_silent(snap(missions=[{**m, "arrive": NOW - timedelta(minutes=5)}]), NOW, D.Config()) == []
    idle = {**m, "phase": "on_scene", "since": NOW - timedelta(minutes=40)}
    assert D.unit_idle(snap(missions=[idle]), NOW, D.Config())[0]["numbers"]["on_scene_min"] == 40
    down = {**m, "phase": "out_of_service"}
    assert D.unit_down(snap([case("m1", 0)], [down]), NOW, D.Config())
    other = {**m, "id": "m1-2", "unit": "ΠΥ-02", "phase": "en_route"}
    assert D.unit_down(snap([case("m1", 0)], [down, other]), NOW, D.Config()) == []


def test_cluster_and_idle_units():
    near = [case(f"m{i}", 50 + i, point=(23.98 + i * 0.001, 38.05)) for i in range(3)]
    far = case("m9", 55, point=(24.10, 38.10))
    [c] = D.cluster(snap(near + [far]), NOW, D.Config())
    assert c["case_ids"] == ["m0", "m1", "m2"] and c["point"] is not None
    waiting = case("m5", 40, decision="DISPATCH_UNIT_CANDIDATE")
    assert D.idle_units(snap([waiting], units=[{"id": "ΠΥ-01", "state": "available"}]), NOW, D.Config())
    assert D.idle_units(snap([waiting], units=[{"id": "ΠΥ-01", "state": "en_route"}]), NOW, D.Config()) == []


# -- agent ------------------------------------------------------------------------------------------------
def test_agent_alerts_are_checked():
    s = snap([case("m1", 50, minutes_left=4.0)])
    findings = D.detect(s, NOW)
    fid = findings[0]["id"]
    out = SupervisorOutput.model_validate({"alerts": [
        {"finding_id": fid, "why_el": "Ειδοποίησε το Λιμενικό.", "action": "alert_coast_guard"},
        {"finding_id": fid, "why_el": "Το m1 έχει μέτωπο σε 4′ χωρίς απόφαση.", "action": "open_case"},
        {"finding_id": "nope", "why_el": "x", "action": "open_case"}]})
    alerts, rejected = check(out, findings)
    assert [a["finding_id"] for a in alerts] == [fid] and len(rejected) == 2
    bad = SupervisorOutput.model_validate(
        {"alerts": [{"finding_id": fid, "why_el": "Μέτωπο σε 3′.", "action": "open_case"}]})
    assert check(bad, findings)[0] == []                                  # 3 is not in the finding


# -- service ----------------------------------------------------------------------------------------------
@pytest.fixture
def desk(tmp_path):
    state = {"snap": snap([case("m1", 50, minutes_left=4.0)])}
    ran = []
    svc = SupervisorService(Registry(tmp_path / "ev.jsonl"), None, lambda now: state["snap"],
                            {"open_case": lambda a, op, now: ran.append(a["case_ids"]) or {"ok": True}})
    return svc, state, ran


def test_tick_raises_once_rechecks_and_resolves(desk):
    svc, state, _ = desk
    t1 = svc.tick(NOW)
    assert len(t1["alerts"]) == 1 and t1["check"] == "rules"
    assert svc.tick(NOW + timedelta(minutes=10))["alerts"] == []          # still open: not raised again
    state["snap"] = snap([case("m1", 50, minutes_left=4.0, status="approved")])
    t3 = svc.tick(NOW + timedelta(minutes=20))
    assert t3["rechecks"][0]["outcome"] == "resolved"
    assert svc.view(NOW + timedelta(minutes=20))["alerts"][0]["status"] == "closed"


def test_escalation_after_the_due_time(desk):
    svc, _, _ = desk
    svc.tick(NOW)
    t = svc.tick(NOW + timedelta(minutes=11))
    assert t["rechecks"][0].get("escalated")
    assert svc.view(NOW + timedelta(minutes=11))["counts"]["escalated"] == 1


def test_accept_runs_the_action_and_only_a_person_decides(desk):
    svc, state, ran = desk
    a = svc.tick(NOW)["alerts"][0]
    with pytest.raises(SupervisorError):
        svc.decide(a["alert_id"], "accept", "anna", NOW, action="alert_coast_guard")   # not allowed for it
    out = svc.decide(a["alert_id"], "accept", "anna", NOW)
    assert out["result"] == {"ok": True} and ran == [["m1"]]
    with pytest.raises(SupervisorError):
        svc.decide(a["alert_id"], "dismiss", "anna", NOW)
    from palladion.engines.registry.registry import Actor, ActorKind
    with pytest.raises(Forbidden):
        svc.registry.append("supervisor", "supervisor.decided", Actor(kind=ActorKind.agent, id="x"), {},
                            expected_version=svc.registry.version("supervisor"))


def test_dismissed_stays_quiet_until_new_cases_join(desk):
    svc, state, _ = desk
    a = svc.tick(NOW)["alerts"][0]
    svc.decide(a["alert_id"], "dismiss", "anna", NOW)
    assert svc.tick(NOW + timedelta(minutes=10))["alerts"] == []
    state["snap"] = snap([case("m1", 50, minutes_left=4.0), case("m2", 65, minutes_left=3.0)])
    assert len(svc.tick(NOW + timedelta(minutes=20))["alerts"]) == 1


def test_agent_wording_or_the_fact_text(tmp_path):
    reg = Registry(tmp_path / "ev.jsonl")
    s = snap([case("m1", 50, minutes_left=4.0)])
    fid = D.detect(s, NOW)[0]["id"]
    import json
    pick = {"finding_id": fid, "why_el": "Το m1 έχει το μέτωπο σε 4′.", "action": "open_case"}
    answer = json.dumps({"alerts": [pick]})
    llm = LLM(ScriptedProvider(lambda a, b, c: answer), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    t = SupervisorService(reg, llm, lambda now: s).tick(NOW)
    assert t["check"] == "agent_checked" and t["alerts"][0]["why_el"].startswith("Το m1")
