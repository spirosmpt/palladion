from datetime import timedelta

from palladion.engines.registry.registry import Registry
from palladion.handover import skeleton as S
from palladion.handover.agent import SitrepOutput, check, summarize
from palladion.handover.service import HandoverService
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider

from .conftest import T0
from .test_supervisor import case, snap

NOW = T0 + timedelta(minutes=60)


def full():
    s = snap([case("m04", 50, minutes_left=6.0, decision="DISPATCH_UNIT_CANDIDATE", people=3),
              case("m14", 40, status="resolved", open=False, rescued=1, field_status="resolved"),
              case("m27", 45, status="proposed", field_status="partial", people=1, rescued=1),
              case("m07", 52, decision="CALLBACK", callback_sent=NOW - timedelta(minutes=8))],
             missions=[{"id": "m27-1", "case": "m27", "unit": "ΕΚΑΒ-01", "phase": "returning", "since": NOW,
                        "arrive": NOW, "label_el": "επιστρέφει", "on_scene_min": 15}])
    s["alerts112"] = []
    s["supervisor_alerts"] = []
    return s


def test_skeleton_lines_come_from_the_records():
    sk = S.build(full(), NOW)
    f = S.facts(sk)
    assert "Μηνύματα ως τις 17:00: 4, με απόφαση χειριστή 1." == f["tot:messages"]
    assert "άτομα που ανέφεραν ως διασωθέντα οι μονάδες ξηράς: 2" in f["tot:field"]
    assert f["case:m04"].startswith("m04 Κυκλάδων, Μάτι: αποστολή μονάδας, μέτωπο σε 6′, άτομα 3")
    assert "χωρίς απάντηση 8′" in f["cb:m07"]
    assert f["unit:ΕΚΑΒ-01"].startswith("ΕΚΑΒ-01 για το m27: επιστρέφει")
    assert f["field:m27"] == "m27: μένει 1 άτομο (αναφορά πεδίου)."
    assert "Αναφορά κατάστασης" in S.as_text(sk)


def test_summary_sentences_cite_lines_and_their_numbers():
    sk = S.build(full(), NOW)
    out = SitrepOutput.model_validate({"sentences": [
        {"text": "Το m04 έχει μέτωπο σε 6′ και 3 άτομα χωρίς απόφαση.", "cites": ["case:m04"]},
        {"text": "Το m04 έχει μέτωπο σε 9′.", "cites": ["case:m04"]},
        {"text": "Όλα καλά.", "cites": ["nope"]},
        {"text": "Στην παραλία περιμένουν 12.", "cites": []}]})
    kept, rejected = check(out, sk)
    assert [k["cites"] for k in kept] == [["case:m04"]] and len(rejected) == 3


def test_create_saves_who_handed_over(tmp_path):
    reg = Registry(tmp_path / "ev.jsonl")
    answer = '{"sentences": [{"text": "Το m04 έχει μέτωπο σε 6′.", "cites": ["case:m04"]}]}'
    llm = LLM(ScriptedProvider(lambda a, b, c: answer), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    svc = HandoverService(reg, llm, lambda now: full())
    h = svc.create(NOW, "anna", note="Η ΠΥ-Μ-01 δεν απαντά στον ασύρματο.")
    assert h["summary"]["status"] == "agent" and h["text"].startswith("Αναφορά κατάστασης")
    assert "Το m04 έχει μέτωπο σε 6′." in h["text"] and "Σημείωση" in h["text"]
    ev = list(reg.events("handover"))[-1]
    assert ev.actor.kind.value == "human" and ev.actor.id == "anna"
    assert svc.history(NOW)[0]["id"] == h["id"]
    assert summarize(None, S.build(full(), NOW))["status"] == "skeleton"
