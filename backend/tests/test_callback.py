import json
from datetime import timedelta

from palladion.agents.callback import SYSTEM, CallbackOutput, Question, check, missing, plan
from palladion.agents.intake import IntakeResult
from palladion.cases.service import CaseService, Status
from palladion.cases.simulator import Reply, Simulator
from palladion.engines.registry.registry import Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0
from .test_agents import intake

TEXT = "Στην Εσπερίδων, ο δρόμος τελειώνει στο δάσος, δεν έχουμε από πού να βγούμε!"


def processed(msg, loc, decision=Decision.CALLBACK, out=None):
    out = out or intake([{"kind": "street", "name": "Εσπερίδων", "quote": "Εσπερίδων"}])
    return Processed(msg, IntakeResult(output=out), loc, None, Outcome(decision, 60, ["r"]))


def test_missing_items_come_from_code():
    m = Message(id="a", time=T0, channel="199_call", text=TEXT)
    p = processed(m, Location("ambiguous", label="Εσπερίδων"))
    assert missing(p) == ["which_area", "people", "can_move"]
    p = processed(m, Location("resolved", 24.0, 38.0, 50, "Εσπερίδων, Μάτι"), Decision.EVACUATE_ROUTE_CANDIDATE,
                  intake([], people={"count": 3, "quote": "3"}, situation={"kind": "home", "quote": "σπίτι"}))
    assert missing(p) == []


def test_questions_are_checked_and_fall_back(tmp_path):
    items = ["which_area", "people"]
    good = CallbackOutput(questions=[Question(item="which_area", text="Σε ποια περιοχή είναι η Εσπερίδων;"),
                                     Question(item="people", text="Πόσοι είστε;")])
    assert check(good, items) == []
    bad = CallbackOutput(questions=[Question(item="people", text="Πόσοι είστε; Στέλνουμε μονάδα, μείνετε μέσα."),
                                    Question(item="address_of_cousin", text="Πού μένει ο ξάδερφος;")])
    problems = " ".join(check(bad, items))
    assert "instructs or promises" in problems and "not missing" in problems

    m = Message(id="a", time=T0, channel="199_call", text=TEXT)
    p = processed(m, Location("ambiguous", label="Εσπερίδων"))
    llm = LLM(ScriptedProvider(lambda s, u, t: json.dumps(bad.model_dump(), ensure_ascii=False)),
              ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    cp = plan(llm, p, "199_call")
    assert cp.source == "fallback" and [q.item for q in cp.questions] == ["which_area", "people", "can_move"]
    assert SYSTEM


def test_loop_sends_questions_and_reads_the_reply(tmp_path):
    msg = Message(id="m07", time=T0, channel="199_call", text=TEXT)

    def triage(m):
        if "Απάντηση" in m.text:      # after the reply the area is known
            return processed(m, Location("resolved", 23.98, 38.05, 300, "Εσπερίδων, Νέος Βουτζάς"),
                             Decision.DISPATCH_UNIT_CANDIDATE)
        return processed(m, Location("ambiguous", label="Εσπερίδων"))

    sim = Simulator({"m07": Reply(3, "Εσπερίδων στον Νέο Βουτζά, 3 άτομα.")})
    svc = CaseService([msg], triage, Registry(tmp_path / "ev.jsonl"),
                      callback=lambda p, ch: plan(None, p, ch), simulator=sim)
    svc.tick(T0, sync=True)
    c = svc.get("m07")
    assert c.callback.source == "fallback" and c.callback_due == T0 + timedelta(minutes=4)
    svc.tick(T0 + timedelta(minutes=3), sync=True)
    assert c.callback_reply is None                                   # not yet
    svc.tick(T0 + timedelta(minutes=5), sync=True)
    assert c.processed.outcome.decision == Decision.DISPATCH_UNIT_CANDIDATE
    assert c.before_callback["decision"] == "CALLBACK" and c.status == Status.proposed
    types = [e.type for e in svc.registry.events("case:m07")]
    assert types == ["case.received", "case.triaged", "callback.sent", "callback.reply", "case.retriaged"]
    reply = [e for e in svc.registry.events("case:m07") if e.type == "callback.reply"][0]
    assert reply.data["simulated"] and reply.data["text"].startswith("[ΠΡΟΣΟΜΟΙΩΣΗ]")


def test_operator_can_stop_a_call_back_and_real_messages_get_no_reply(tmp_path):
    msg = Message(id="real1", time=T0, channel="sms", text=TEXT)
    svc = CaseService([msg], lambda m: processed(m, Location("ambiguous", label="Εσπερίδων")),
                      Registry(tmp_path / "ev.jsonl"), callback=lambda p, ch: plan(None, p, ch),
                      simulator=Simulator({}))
    svc.tick(T0, sync=True)
    c = svc.get("real1")
    assert c.callback is not None and c.callback_due is None          # asked, but nobody simulates an answer
    svc.act("real1", "cancel_callback", "op", T0 + timedelta(minutes=1))
    assert c.callback_cancelled


def test_questions_use_the_senders_script():
    items = ["people"]
    greeklish = CallbackOutput(questions=[Question(item="people", text="Posa atoma eiste mazi;")])
    assert any("script" in x for x in check(greeklish, items, TEXT))
    assert check(greeklish, items, "eimaste sto mati, voitheia") == []
