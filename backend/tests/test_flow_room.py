from datetime import timedelta
from types import SimpleNamespace

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.flow.room import RoomFlow

from .conftest import T0

CALL = {"role": "supervisor", "model": "gemini-x", "status": "cached", "seconds": 0.0, "original_seconds": 2.4}


def add(reg, stream, etype, data, minutes, human=False):
    actor = Actor(kind=ActorKind.human if human else ActorKind.system, id="anna" if human else "desk")
    reg.append(stream, etype, actor, data, expected_version=reg.version(stream),
               sim_time=T0 + timedelta(minutes=minutes))


def test_room_agents_runs_states_and_real_seconds(tmp_path):
    reg = Registry(tmp_path / "ev.jsonl")
    add(reg, "supervisor", "supervisor.tick", {"findings": [], "alerts": [], "check": "no_findings", "call": None}, 5)
    add(reg, "supervisor", "supervisor.tick", {"findings": [{"id": "f1"}], "check": "agent_checked", "call": CALL,
                                               "alerts": [{"alert_id": "sv-1", "why_el": "Ξεχασμένο m3"}]}, 10)
    add(reg, "supervisor", "supervisor.decided", {"alert_id": "sv-1", "decision": "accept"}, 12, human=True)
    add(reg, "unit:L1", "unit.dispatched", {"case": "m3", "brief": "Πηγαίνετε…", "brief_source": "template",
                                           "brief_calls": [{**CALL, "status": "ok", "seconds": 1.1}],
                                           "brief_problems": ["αριθμός 9 που δεν υπάρχει"]}, 15, human=True)
    add(reg, "unit:L1", "unit.report", {"unit": "L1", "text": "φτάσαμε", "check": "rules", "findings":
                                       [{"kind": "arrived", "quote": "φτάσαμε", "value": True}]}, 20)
    add(reg, "handover", "handover.created", {"summary": {"status": "skeleton", "sentences": [], "rejected": [],
                                                          "call": None}}, 25, human=True)
    written = SimpleNamespace(source="agent", problems=[], call={**CALL, "status": "ok", "seconds": 3.0},
                              text=SimpleNamespace(el="Εκκενώστε προς Ραφήνα"))
    alert = SimpleNamespace(id="a1", created=T0 + timedelta(minutes=30), written=written, edited=False,
                            status=SimpleNamespace(value="sent"), threat=SimpleNamespace(settlement="Μάτι"))
    alerts = SimpleNamespace(visible=lambda: [alert])

    v = RoomFlow(lambda: reg, lambda: alerts).view(T0 + timedelta(minutes=29))
    assert [r["agent"] for r in v["runs"]] == ["sitrep", "field_report", "crew_brief", "supervisor"]  # alert later
    by = {r["agent"]: r for r in v["runs"]}
    assert by["supervisor"]["seconds"] == 2.4 and by["supervisor"]["cached"]       # the real call's time
    assert "αποφάσισε για 1 από 1" in by["supervisor"]["human"]
    assert by["crew_brief"]["state"] == "rejected" and by["crew_brief"]["seconds"] == 1.1
    assert by["field_report"]["state"] == "code" and "έφτασαν" in by["field_report"]["wrote"]
    assert by["sitrep"]["state"] == "code"
    sup = next(a for a in v["agents"] if a["id"] == "supervisor")
    assert sup["runs"] == 1 and sup["avg_s"] == 2.4                                  # an empty tick is not a run

    v = RoomFlow(lambda: reg, lambda: alerts).view(T0 + timedelta(minutes=40))
    a = v["runs"][0]
    assert a["agent"] == "alert_writer" and a["state"] == "model" and a["human"] == "στάλθηκε"
