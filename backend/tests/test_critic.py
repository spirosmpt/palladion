import json

from palladion.agents.critic import REVISION_NOTE, review, triggers
from palladion.agents.critic import SYSTEM as CRITIC_SYSTEM
from palladion.agents.intake import IntakeOutput, IntakeResult, run_intake
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider

MSG = "Είμαστε 3, όχι, 5 με τα παιδιά, στα βράχια κάτω από την Ποσειδώνος. Βοήθεια!"

FIRST = {  # misses the correction and reads the rocks as the beach
    "kind": "help_request", "reporter": "self", "language": "el",
    "people": {"count": 3, "quote": "Είμαστε 3"},
    "vulnerable": [], "situation": {"kind": "beach", "quote": "στα βράχια"},
    "places": [{"kind": "street", "name": "Ποσειδώνος", "quote": "Ποσειδώνος"}],
    "hazards": [], "asks_for": ["rescue"], "summary_el": "Τρία άτομα στα βράχια.",
}
OBJECTIONS = {"objections": [
    {"field": "people", "reason": "overstated", "quote": "όχι, 5 με τα παιδιά", "note": "corrected to 5"},
    {"field": "vulnerable", "reason": "missed", "quote": "με τα παιδιά"},
    {"field": "situation", "reason": "unsupported", "quote": "στα βράχια"},
    {"field": "people", "reason": "missed", "quote": "δέκα άτομα"},          # not in the message: dropped
]}
SECOND = {**FIRST, "people": {"count": 5, "quote": "5 με τα παιδιά"},
          "vulnerable": [{"kind": "child", "quote": "με τα παιδιά"}],
          "situation": {"kind": "sea", "quote": "στα βράχια"}}


def scripted(tmp_path, critic=OBJECTIONS, second=SECOND):
    def answer(system, user, tier):
        if system.startswith(CRITIC_SYSTEM):
            return json.dumps(critic, ensure_ascii=False)
        if REVISION_NOTE in system:
            return json.dumps(second, ensure_ascii=False)
        return json.dumps(FIRST, ensure_ascii=False)
    provider = ScriptedProvider(answer)
    return LLM(provider, ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl"), provider


def test_triggers_only_when_there_is_a_reason():
    first = IntakeResult(output=IntakeOutput.model_validate(FIRST))
    assert triggers(first, MSG) == ["several_numbers"]
    plain = IntakeResult(output=IntakeOutput.model_validate({**FIRST, "people": {"count": 2, "quote": "Είμαστε 2"}}))
    assert triggers(plain, "Είμαστε 2 στα βράχια κάτω από την Ποσειδώνος.") == []
    safe = IntakeResult(output=IntakeOutput.model_validate({**FIRST, "kind": "safety_report"}))
    assert "safe_but_asks_for_help" in triggers(safe, MSG)


def test_critic_objections_drive_one_revision(tmp_path):
    llm, provider = scripted(tmp_path)
    first = run_intake(llm, MSG, "sms")
    used, rv = review(llm, first, MSG, "sms")
    assert rv is not None and rv.revised and len(rv.objections) == 3 and rv.dropped == ["people: «δέκα άτομα»"]
    assert used.output.people.count == 5 and used.output.situation.kind.value == "sea"
    assert any(c.startswith("άτομα: 3 → 5") for c in rv.changes)
    assert provider.calls == 3                          # intake, critic, revision


def test_no_valid_objection_means_no_revision(tmp_path):
    llm, provider = scripted(tmp_path / "a", critic={"objections": [
        {"field": "people", "reason": "missed", "quote": "εκατό άτομα"}]})
    first = run_intake(llm, MSG, "sms")
    used, rv = review(llm, first, MSG, "sms")
    assert used is first and rv is not None and not rv.revised and provider.calls == 2


def test_revision_quotes_are_checked_too(tmp_path):
    bad = {**SECOND, "people": {"count": 9, "quote": "είμαστε εννιά"}}         # invented in the revision
    llm, _ = scripted(tmp_path, second=bad)
    used, rv = review(llm, run_intake(llm, MSG, "sms"), MSG, "sms")
    assert used.output.people is None or used.output.people.count != 9
    assert any("people" in r for r in used.rejected)
