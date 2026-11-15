from datetime import timedelta

import pytest

from palladion.cases.service import CaseError, CaseService
from palladion.engines.registry.registry import Registry
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed
from palladion.pipeline.redact import redact

from .conftest import T0


def test_personal_data_is_masked_and_addresses_and_counts_are_not():
    keep = "Κυκλάδων 12 στο Μάτι, είμαστε 4, ο παππούς 82 χρονών, 18:40, Ποσειδώνος 32"
    assert redact(keep) == (keep, [])
    text = "Πάρτε με στο 694 123 4567, email nikos.p@gmail.com, ΑΦΜ: 123456789, αυτοκίνητο ΥΖΑ-1234"
    masked, kinds = redact(text)
    assert len(masked) == len(text) and "694" not in masked and "@" not in masked and "123456789" not in masked
    assert set(kinds) == {"phone", "email", "afm", "plate"} and "ΑΦΜ:" in masked       # the label stays
    assert redact("my dad is at +44 7700 900123")[1] == ["phone"]


def test_the_agents_never_see_the_phone_number(tmp_path):
    from palladion.geo.gazetteer import Gazetteer
    from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
    from palladion.pipeline.process import process
    seen = []

    def answer(system, user, tier):
        seen.append(user)
        raise RuntimeError("stop after the first call")
    llm = LLM(ScriptedProvider(answer), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    m = Message(id="x", time=T0, channel="sms", text="Είμαστε 3 στο Μάτι, πάρτε με στο 6941234567")
    with pytest.raises(RuntimeError):
        process(m, llm, Gazetteer([]), None, (0, 0, 1, 1))
    assert seen and "6941234567" not in seen[0] and "••••••••••" in seen[0]


def _triage(m):
    found = "Κυκλάδων" in m.text
    loc = Location("resolved", 23.99, 38.05, 100, "Κυκλάδων, Μάτι") if found else Location("approximate")
    decision = Decision.DISPATCH_UNIT_CANDIDATE if found else Decision.CALLBACK
    return Processed(m, None, loc, 20, Outcome(decision, 60, ["r"]))


def test_the_senders_answer_is_read_again_and_kept(tmp_path):
    reg = Registry(tmp_path / "ev.jsonl")
    msgs = [Message(id="m1", time=T0, channel="sms", text="Βοήθεια, είμαστε στο Μάτι")]
    svc = CaseService(msgs, _triage, reg)
    now = T0 + timedelta(minutes=5)
    svc.tick(now, sync=True)
    assert svc.get("m1").processed.outcome.decision == Decision.CALLBACK
    svc.act("m1", "approve", "anna", now)
    with pytest.raises(CaseError):
        svc.answer("m1", " ", "anna", now)
    c = svc.answer("m1", "Στην Κυκλάδων, δίπλα στην εκκλησία", "anna", now + timedelta(minutes=2), sync=True)
    assert c.processed.outcome.decision == Decision.DISPATCH_UNIT_CANDIDATE
    assert c.status.value == "proposed" and c.reopened_by          # a handled case comes back to the queue
    assert "[Απάντηση στην επανάκληση] Στην Κυκλάδων" in c.processed.message.text
    assert c.replies[0]["by"] == "anna" and not c.replies[0]["simulated"]
    ev = [e for e in reg.events("case:m1") if e.type == "callback.answered"][0]
    assert ev.actor.kind.value == "human"
    # After a restart the answer comes back and the case is read with it again.
    again = CaseService(msgs, _triage, Registry(tmp_path / "ev.jsonl"))
    again.tick(now + timedelta(minutes=3), sync=True)
    r = again.get("m1")
    assert r.replies[0]["text"].startswith("Στην Κυκλάδων")
    assert r.processed.outcome.decision == Decision.DISPATCH_UNIT_CANDIDATE


def test_text_from_outside_is_cleaned_once():
    from palladion.textclean import clean
    assert clean("Κυ​κλά‍δων στο Μά﻿τι") == "Κυκλάδων στο Μάτι"
    assert clean("\x00\x07Μάτι \x1b[31mβοήθεια") == "Μάτι [31mβοήθεια"
    assert clean("Μάτι \ud83d βοήθεια") == "Μάτι � βοήθεια"
    assert clean("γραμμή 1\nγραμμή 2\tκαι 🔥") == "γραμμή 1\nγραμμή 2\tκαι 🔥"


def test_no_news_for_twenty_minutes_proposes_still_there(tmp_path):
    from palladion.cases.service import no_news
    from palladion.supervisor.detectors import Config
    from palladion.supervisor.detectors import no_news as watch
    msgs = [Message(id="m1", time=T0, channel="sms", text="Βοήθεια στην Κυκλάδων")]
    svc = CaseService(msgs, _triage, Registry(tmp_path / "ev.jsonl"))
    svc.tick(T0 + timedelta(minutes=1), sync=True)
    c = svc.get("m1")
    assert no_news(c, T0 + timedelta(minutes=19)) is None
    assert no_news(c, T0 + timedelta(minutes=25))["minutes"] == 25
    svc.answer("m1", "Ναι, στην Κυκλάδων ακόμα", "anna", T0 + timedelta(minutes=24), sync=True)
    assert no_news(svc.get("m1"), T0 + timedelta(minutes=30)) is None        # the answer is news
    svc.act("m1", "close", "anna", T0 + timedelta(minutes=31))
    assert no_news(svc.get("m1"), T0 + timedelta(minutes=90)) is None        # nobody is waiting any more
    row = {"id": "m2", "label": "Ζακύνθου", "minutes_left": 5, "no_news_min": 27, "callback_sent": None,
           "callback_reply": None, "callback_cancelled": False}
    [f] = watch({"cases": [row]}, T0, Config())
    assert f["kind"] == "no_news" and f["case_ids"] == ["m2"] and "27′" in f["text_el"]
