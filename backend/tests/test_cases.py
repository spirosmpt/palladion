from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from palladion.api.app import create_app
from palladion.cases.service import CaseError, CaseService, Status, minutes_left, urgency
from palladion.config import REPO_ROOT, Settings
from palladion.engines.registry.registry import Registry
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0


def msg(id_, minutes, text="x"):
    return Message(id=id_, time=T0 + timedelta(minutes=minutes), channel="sms", text=text)


def fake_triage(front_after: dict[str, float | None]):
    def triage(m: Message) -> Processed:
        if m.id == "boom":
            raise RuntimeError("model down")
        return Processed(m, None, Location("resolved", 23.98, 38.05, 200, "Οδός"), front_after.get(m.id),
                         Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, 60, ["r"]))
    return triage


def service(tmp_path, front):
    msgs = [msg("a", 0), msg("b", 5), msg("c", 10), msg("boom", 12)]
    return CaseService(msgs, fake_triage(front), Registry(tmp_path / "ev.jsonl"))


def test_messages_arrive_with_the_clock_and_failures_stay_visible(tmp_path):
    svc = service(tmp_path, {"a": 30, "b": 8, "c": None})
    svc.tick(T0 + timedelta(minutes=6), sync=True)
    assert {c.message.id for c in svc.visible(T0 + timedelta(minutes=6))} == {"a", "b"}
    svc.tick(T0 + timedelta(minutes=20), sync=True)
    assert svc.get("boom").status == Status.failed
    assert svc.get("a").status == Status.proposed


def test_deadline_queue_order_and_escalation(tmp_path):
    svc = service(tmp_path, {"a": 30, "b": 8, "c": None})
    now = T0 + timedelta(minutes=10)
    svc.tick(now, sync=True)
    order = [c.message.id for c in svc.visible(now)]
    # b: front in 8-5=3 min from now; a: 30 min after 16:00 -> 20 min from now; c: no fire.
    assert order[:2] == ["b", "a"]
    b = svc.get("b")
    assert minutes_left(b, now) == pytest.approx(3) and urgency(b, now) == "urgent"
    later = T0 + timedelta(minutes=14)
    assert urgency(b, later) == "overdue"
    svc.act("b", "approve", "op1", later)
    assert urgency(svc.get("b"), later) == "normal"
    assert [c.message.id for c in svc.visible(later)][-1] == "b"     # handled cases go to the bottom


def test_human_actions_are_recorded_and_survive_restart(tmp_path):
    svc = service(tmp_path, {"a": 30})
    now = T0 + timedelta(minutes=1)
    svc.tick(now, sync=True)
    svc.act("a", "change", "op1", now, decision="DISPATCH_UNIT_CANDIDATE", note="ηλικιωμένοι")
    with pytest.raises(CaseError):
        svc.act("a", "change", "op1", now, decision="NOT_A_DECISION")
    types = [e.type for e in svc.registry.events("case:a")]
    assert types == ["case.received", "case.triaged", "case.decision_changed"]

    again = CaseService([msg("a", 0)], fake_triage({"a": 30}), Registry(tmp_path / "ev.jsonl"))
    again.tick(now, sync=True)
    c = again.get("a")
    assert c.status == Status.changed and c.final_decision == "DISPATCH_UNIT_CANDIDATE" and c.operator == "op1"
    assert [e.type for e in again.registry.events("case:a")].count("case.triaged") == 1


def test_an_action_on_a_case_that_changed_since_it_was_opened_is_refused(tmp_path):
    from palladion.cases.service import StaleCase
    svc = service(tmp_path, {"a": 30})
    now = T0 + timedelta(minutes=1)
    svc.tick(now, sync=True)
    seen = svc.version("a")
    first = next(iter(svc.registry.events("case:a")))
    svc.registry.append("case:a", "case.callback_reply", first.actor, {"text": "νέο"},
                        expected_version=seen, sim_time=now)                  # something happened meanwhile
    with pytest.raises(StaleCase):
        svc.act("a", "approve", "op1", now, expected_version=seen)
    svc.act("a", "approve", "op1", now, expected_version=svc.version("a"))
    assert svc.get("a").status == Status.approved


def test_cases_api(tmp_path):
    s = Settings(scenario_dir=REPO_ROOT / "scenarios" / "mati_2018", data_dir=tmp_path / "data",
                 runtime_dir=tmp_path / "rt", llm_cache_dir=tmp_path / "cache", case_worker=False,
                 llm_provider="replay")
    (tmp_path / "data").mkdir()
    with TestClient(create_app(s)) as client:
        client.post("/api/clock", json={"action": "seek", "time": "2018-07-23T17:50:00+03:00"})
        st = client.app.state.s
        st.cases.tick(st.clock.state().sim_time, sync=True)
        body = client.get("/api/cases").json()
        assert body["llm"]["provider"] == "replay"
        assert [c["id"] for c in body["cases"]] and all(c["time"] <= body["now"] for c in body["cases"])
        first = body["cases"][0]
        # No recorded answers: the agent fails visibly and the case waits for a person.
        assert first["status"] == "failed" and first["decision"] == "REVIEW_REQUIRED" and first["error"]
        stale = client.post(f"/api/cases/{first['id']}/action", json={"action": "change", "decision": "CALLBACK",
                                                                      "expected_version": first["version"] - 1})
        assert stale.status_code == 409 and "άλλαξε" in stale.json()["detail"]
        r = client.post(f"/api/cases/{first['id']}/action",
                        json={"action": "change", "decision": "CALLBACK", "operator": "eleni",
                              "expected_version": first["version"]})
        assert r.status_code == 200 and r.json()["status"] == "changed"
        assert client.post("/api/cases/zzz/action", json={"action": "approve"}).status_code == 409
        detail = client.get(f"/api/cases/{first['id']}").json()
        assert [e["type"] for e in detail["events"]][-1] == "case.decision_changed"
        assert detail["events"][-1]["actor"] == {"kind": "human", "id": "eleni"}
        reset = client.post("/api/session/reset").json()
        assert reset["sim_time"].startswith("2018-07-23T16:30")
        assert client.get("/api/cases").json()["cases"] == []


def test_no_deadline_for_safe_or_informational_cases(tmp_path):
    def triage(m):
        d = Decision.REPORTED_SAFE if m.id == "a" else Decision.EVACUATE_ROUTE_CANDIDATE
        return Processed(m, None, Location("resolved", 23.98, 38.05, 200, "x"), 1, Outcome(d, 5, []))
    svc = CaseService([msg("a", 0), msg("b", 0)], triage, Registry(tmp_path / "ev.jsonl"))
    now = T0 + timedelta(minutes=5)
    svc.tick(now, sync=True)
    assert urgency(svc.get("a"), now) == "normal" and urgency(svc.get("b"), now) == "overdue"
    assert [c.message.id for c in svc.visible(now)] == ["b", "a"]


def test_an_operator_types_in_a_call(tmp_path):
    """A call taken by phone goes through the same agents and queue; entering it is not acting on it."""
    seen = []

    def triage(m):
        seen.append(m)
        loc = Location("resolved", *m.point, 50, "σημείο") if m.point else Location("none")
        return Processed(m, None, loc, 30, Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 60, ["r"]))
    reg = Registry(tmp_path / "ev.jsonl")
    svc = CaseService([], triage, reg)
    now = T0 + timedelta(minutes=20)
    with pytest.raises(CaseError):
        svc.submit("Βοήθεια", "pigeon", "anna", now)
    with pytest.raises(CaseError):
        svc.submit("Βοήθεια στην Κυκλάδων", "199_call", "anna", now, received_at=now + timedelta(minutes=1))
    m = svc.submit("Κυκλάδων στο Μάτι, 3 άτομα", "199_call", "anna", now, received_at=now - timedelta(minutes=4),
                   point=(23.99, 38.05), sync=True)
    assert m.id == "op01" and seen[0].point == (23.99, 38.05) and seen[0].entered_by == "anna"
    c = svc.get("op01")
    assert c.status.value == "proposed" and c.operator is None and c.message.time == now - timedelta(minutes=4)
    first = next(reg.events("case:op01"))
    assert first.type == "case.submitted" and first.actor.kind.value == "human"
    # After a restart the typed-in message is back, still waiting for a decision.
    again = CaseService([], triage, Registry(tmp_path / "ev.jsonl"))
    again.tick(now, sync=True)
    assert again.get("op01").status.value == "proposed" and again.get("op01").operator is None
    assert svc.submit("Δεύτερη κλήση, Αιγαίου", "radio", "anna", now).id == "op02"
