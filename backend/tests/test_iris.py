import json

from palladion.agents.iris import Iris, compare

ANS = {"action": {"type": "choice", "choice": "callback", "confidence": 0.75,
                  "probabilities": {"dispatch": 0.16, "callback": 0.83, "info": 0.01}},
       "urgent": {"type": "noul", "noul": 0.98}, "genuine": {"type": "noul", "noul": 0.9}}


def test_code_compares_and_flags_without_choosing():
    assert compare(ANS, "CALLBACK")["verdict"] == "agrees"
    d = compare(ANS, "INFO")
    assert d["verdict"] == "disagrees" and "iris_disagrees" in d["flags"] and "iris_urgent" in d["flags"]
    assert compare(ANS, "DISPATCH_UNIT_CANDIDATE")["verdict"] == "disagrees"
    assert compare(ANS, "MISSING_PERSON")["verdict"] == "disagrees"
    weak = {**ANS, "action": {**ANS["action"], "confidence": 0.4}}
    assert compare(weak, "INFO")["verdict"] == "unsure"


def test_answers_are_cached_by_text(tmp_path, monkeypatch):
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {"answers": ANS}

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(k["json"]) or R())
    i = Iris("k", "P-1", tmp_path)
    assert i.ask("Μήνυμα") == ANS and i.ask("Μήνυμα") == ANS and len(calls) == 1
    assert calls[0]["model"] == "projects/P-1/models/iris-latest"
    assert set(calls[0]["questions"]) == {"action", "urgent", "genuine"}
    assert Iris("k", "P-1", tmp_path).ask("Μήνυμα") == ANS and len(calls) == 1      # from disk after a restart
    assert json.loads((tmp_path / "iris.jsonl").read_text(encoding="utf-8").splitlines()[0])["answers"] == ANS
