import json

from palladion.agents.credibility import SYSTEM, review_credibility, signals
from palladion.agents.intake import IntakeOutput, IntakeResult
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider


def llm_says(answer, tmp_path):
    def fn(system, user, tier):
        assert system.startswith(SYSTEM)
        return json.dumps(answer, ensure_ascii=False)
    return LLM(ScriptedProvider(fn), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def test_signals_quote_the_message_and_ignore_panic():
    assert signals("ΒΟΗΘΕΙΑ!!! ΚΑΙΓΟΜΑΣΤΕ ΣΤΗΝ ΚΥΑΝΗΣ ΑΚΤΗΣ", None, "resolved", 5, []) == []
    assert signals("ακούσαμε φωνές από το σπίτι δίπλα", None, "resolved", 5, []) == []      # witnessed
    s = signals("Λένε ότι καίγεται η Νέα Μάκρη", None, "approximate", None, [])
    assert [(x.id, x.strength, x.quote) for x in s] == [("hearsay", "weak", "Λένε ότι")]
    ids = {x.id for x in signals("για πλάκα κάνω, στείλτε στο https://x.y", None, "none", None, [])}
    assert ids == {"hoax_marker", "money_or_link"}
    big = IntakeResult(output=IntakeOutput.model_validate({
        "kind": "help_request", "reporter": "self", "language": "el",
        "people": {"count": 150, "quote": "150 άτομα"}, "situation": {"kind": "home", "quote": "στο σπίτι"},
        "summary_el": "-"}))
    assert [x.id for x in signals("150 άτομα στο σπίτι", big, "resolved", 5, [])] == ["impossible_headcount"]


def test_likely_false_needs_a_strong_signal(tmp_path):
    weak = signals("Λένε ότι καίγεται η Νέα Μάκρη", None, "approximate", None, [])
    rv = review_credibility(llm_says({"assessment": "likely_false", "reason_el": "Φήμη."}, tmp_path / "a"),
                            "Λένε ότι καίγεται η Νέα Μάκρη", weak)
    assert rv.assessment == "doubtful" and rv.capped
    strong = signals("για πλάκα κάνω", None, "none", None, [])
    rv = review_credibility(llm_says({"assessment": "LIKELY_FALSE", "reason_el": "Πλάκα."}, tmp_path / "b"),
                            "για πλάκα κάνω", strong)
    assert rv.assessment == "likely_false" and not rv.capped
