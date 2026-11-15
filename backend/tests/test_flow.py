from datetime import timedelta

from palladion.agents.intake import IntakeOutput, IntakeResult, MessageKind, Reporter
from palladion.cases.service import CaseService
from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.flow.service import FlowBuilder
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0, ll

CALL = {"role": "intake", "model": "gemini-x", "status": "cached", "seconds": 0.0, "original_seconds": 1.8}


def triage(m):
    out = IntakeOutput(kind=MessageKind.help_request, reporter=Reporter.self_, language="el", summary_el="-")
    intake = IntakeResult(output=out, call=CALL)
    p = Processed(m, intake, Location("resolved", *ll(2, 0), 50, "Οδός"), 12.0,
                  Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 80, ["r"]))
    p.hazard_review = {"call": {**CALL, "status": "error", "role": "hazard_critic"}, "cautions": [], "changed": None}
    return p


def test_the_pipeline_and_the_people_in_one_stream(tmp_path):
    reg = Registry(tmp_path / "ev.jsonl")
    msgs = [Message(id="m1", time=T0 + timedelta(minutes=10), channel="sms", text="Βοήθεια, καιγόμαστε")]
    cases = CaseService(msgs, triage, reg)
    cases.tick(T0 + timedelta(minutes=20), sync=True)
    reg.append("case:m1", "case.approved", Actor(kind=ActorKind.human, id="anna"),
               {"decision": "DISPATCH_UNIT_CANDIDATE"}, expected_version=reg.version("case:m1"),
               sim_time=T0 + timedelta(minutes=15))
    v = FlowBuilder(lambda: cases, lambda: reg).view(T0 + timedelta(minutes=20))
    ev = v["events"]
    assert ev[0]["src"] == "citizen" and ev[0]["dst"] == "intake"
    intake = next(e for e in ev if e["src"] == "intake")
    assert intake["seconds"] == 1.8 and intake["cached"] and intake["model"] == "gemini-x"   # the real call's time
    assert next(e for e in ev if e["src"] == "hazard")["status"] == "failed"
    gate = next(e for e in ev if e["src"] == "decide")
    assert gate["human_gate"] and "αποστολή μονάδας" in gate["payload"]
    assert ev[-1]["kind"] == "human_action" and ev[-1]["payload"].startswith("έγκριση")
    node = next(n for n in v["nodes"] if n["id"] == "intake")
    assert node["calls"] == 1 and node["avg_s"] == 1.8
    only = FlowBuilder(lambda: cases, lambda: reg).view(T0 + timedelta(minutes=12), case_id="m1")
    assert all(e["case"] == "m1" for e in only["events"]) and not any(e["kind"] == "human_action"
                                                                       for e in only["events"])
