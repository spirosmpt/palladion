from datetime import timedelta

from palladion.agents.needs import Proposed, check, read, rules
from palladion.cases.service import CaseService, deadline, deadline_left, minutes_left
from palladion.engines.registry.registry import Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0


def tags(text):
    return {n.tag for n in rules(text)[0]}


def test_rules_read_needs_with_quotes_and_skip_denials():
    assert tags("Ο πατέρας μου έχει άσθμα και δεν μπορεί να αναπνεύσει") == {"breathing"}
    assert tags("Κανένας τραυματίας, είμαστε καλά") == set()
    assert tags("Παιδιά, η μάνα μου δεν σηκώνει τηλέφωνο") == set()          # "guys", and the phone
    assert tags("Είμαι με τα δύο παιδιά μου και τον σκύλο") == {"children", "animals"}
    assert tags("My grandparents are over 80, grandad can't walk") == {"elderly", "bedridden"}
    needs, sender = rules("Ο θείος μου είναι κατάκοιτος στη Φυσιολατρών")
    assert needs[0].quote == "κατάκοιτος" and sender == {"role": "relative", "quote": "Ο θείος μου"}


def test_acuity_and_the_medical_clock_are_code():
    burns = read(None, "Ναρκίσσου, ένας άντρας με εγκαύματα στα χέρια")
    assert burns["acuity"] == "critical" and burns["clock_min"] == 15 and burns["ambulance"]
    bed = read(None, "η γιαγιά είναι κατάκοιτη")
    assert bed["acuity"] == "immobile" and bed["clock_min"] is None and not bed["ambulance"]
    assert read(None, "Είμαστε 3 στο σπίτι")["acuity"] is None


def test_agent_needs_are_checked(tmp_path):
    text = "Ο παππούς έπεσε και πονάει, δεν είναι καλά. Κανένα μωρό εδώ."
    assert check(Proposed(tag="injury", quote="έπεσε και πονάει"), text) is None
    assert check(Proposed(tag="infant", quote="μωρό"), text)                    # denied: "Κανένα μωρό"
    assert check(Proposed(tag="burns", quote="καμένος"), text)                  # not in the message
    answer = ('{"needs": [{"tag": "injury", "quote": "έπεσε και πονάει"}, {"tag": "infant", "quote": "μωρό"},'
              ' {"tag": "elderly", "quote": "Ο παππούς"}]}')
    llm = LLM(ScriptedProvider(lambda a, b, c: answer), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    r = read(llm, text)
    assert r["check"] == "agent_checked" and r["acuity"] == "serious" and len(r["rejected"]) == 1
    # The rules saw the grandfather; only the agent saw that he fell and is in pain, and it is in the message.
    assert {n["tag"]: n["source"] for n in r["needs"]} == {"elderly": "rules", "injury": "agent"}


def test_a_medical_clock_earlier_than_the_front_moves_the_case_up(tmp_path):
    def triage(m):
        p = Processed(m, None, Location("resolved", 23.98, 38.05, 200, "Οδός"), 60,
                      Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 60, ["r"]))
        p.needs = read(None, m.text)
        return p
    msgs = [Message(id="far", time=T0, channel="sms", text="Είμαστε 3 στο σπίτι"),
            Message(id="burn", time=T0 + timedelta(minutes=5), channel="sms", text="ένας με εγκαύματα")]
    svc = CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"))
    now = T0 + timedelta(minutes=6)
    svc.tick(now, sync=True)
    c = svc.get("burn")
    assert deadline(c)[1] == "medical" and round(deadline_left(c, now)) == 14 and round(minutes_left(c, now)) == 59
    assert [x.message.id for x in svc.visible(now)] == ["burn", "far"]


def test_the_queue_says_why_a_case_is_where_it_is(tmp_path):
    from palladion.cases.service import why_here

    def triage(m):
        p = Processed(m, None, Location("resolved", 23.98, 38.05, 200, "Οδός"), 60,
                      Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 60, ["r"]))
        p.needs = read(None, m.text)
        return p
    msgs = [Message(id="far", time=T0, channel="sms", text="Είμαστε 3 στο σπίτι"),
            Message(id="burn", time=T0, channel="sms", text="ένας με εγκαύματα")]
    svc = CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"))
    now = T0 + timedelta(minutes=5)
    svc.tick(now, sync=True)
    burn = " ".join(x["el"] for x in why_here(svc.get("burn"), now))
    assert "Ιατρική προθεσμία (εγκαύματα, 15′" in burn and "μένουν 10′" in burn
    assert "νωρίτερα από το μέτωπο (55′)" in burn
    far = " ".join(x["el"] for x in why_here(svc.get("far"), now))
    assert "Το μέτωπο φτάνει σε 55′" in far
    svc.act("far", "close", "anna", now)
    assert why_here(svc.get("far"), now)[0]["el"].startswith("Διεκπεραιώθηκε")
