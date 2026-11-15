import json
from datetime import timedelta

from palladion.agents.contra_question import ask, check
from palladion.cases.service import CaseService
from palladion.engines.registry.registry import Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.process import Message

from .conftest import T0
from .test_contradictions import SETUP, TEXT, triage

COUNT = {"kind": "count_linked", "a": {"source": "message", "ref": "a", "quote": "Είμαστε 3"},
         "b": {"source": "linked_message", "ref": "b", "quote": "Είμαστε 5 — η Μαρία"},
         "question_el": "Πόσοι ακριβώς είστε μαζί τώρα;"}
MSG = "Είμαστε 3 στην Κυκλάδων"


def llm_says(tmp_path, text):
    return LLM(ScriptedProvider(lambda s, u, t: json.dumps({"question": text}, ensure_ascii=False)),
               ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def test_code_checks_the_question():
    assert check("Γράψατε ότι είστε 3 στην Κυκλάδων. Είναι κάποιος άλλος μαζί σας τώρα;", COUNT, MSG) == []
    assert check("Είστε 3 ή 5;", COUNT, MSG) == []                                   # the other side's number: fine
    assert any("number" in p for p in check("Είστε 7 άτομα;", COUNT, MSG))
    assert any("name" in p for p in check("Είναι μαζί σας η Μαρία;", COUNT, MSG))  # another sender's words
    assert any("instructs" in p for p in check("Πόσοι είστε; Μείνετε μέσα.", COUNT, MSG))
    assert any("model" in p for p in check("Το μοντέλο λέει ότι η φωτιά είναι κοντά. Τη βλέπετε;", COUNT, MSG))
    assert any("question" in p for p in check("Πείτε μας πόσοι είστε.", COUNT, MSG))
    assert any("script" in p for p in check("How many are you?", COUNT, MSG))


def test_agent_question_kept_or_the_fixed_one_stays(tmp_path):
    good = ask(llm_says(tmp_path / "g", "Γράψατε 3 στην Κυκλάδων· είναι κάποιος άλλος μαζί σας;"), COUNT, MSG)
    assert good.source == "agent" and "Κυκλάδων" in good.text
    bad = ask(llm_says(tmp_path / "b", "Είναι μαζί σας η Μαρία; Μείνετε εκεί."), COUNT, MSG)
    assert bad.source == "fixed" and bad.text == COUNT["question_el"] and bad.problems
    assert ask(None, COUNT, MSG).source == "fixed"


def test_the_desk_shows_the_agents_question_once_written(tmp_path):
    msgs = [Message(id=k, time=T0 + timedelta(minutes=i), channel="sms", text=TEXT[k]) for i, k in enumerate(SETUP)]
    llm = llm_says(tmp_path, "Βλέπετε τις φλόγες στην αυλή τώρα; Από ποια μεριά έρχονται;")
    s = CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"),
                    contra_ask=lambda item, text, lang: ask(llm, item, text, lang))
    s.tick(T0 + timedelta(minutes=10), sync=True)
    it = s.contradictions("flames")[0]
    assert it["question_source"] == "pending" and it["question_el"] == it["question_fixed_el"]
    s.contradiction_question("flames", it, T0 + timedelta(minutes=12))
    it = s.contradictions("flames")[0]
    assert it["question_source"] == "agent" and it["question_el"].startswith("Βλέπετε τις φλόγες")
    assert it["question_fixed_el"] != it["question_el"]
    s.contradiction_question("flames", it, T0 + timedelta(minutes=13))              # written once per item
    assert sum(e.type == "case.contradiction_question" for e in s.registry.events("case:flames")) == 1
