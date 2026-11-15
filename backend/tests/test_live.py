import time

from palladion.live import RateLimit

from .test_api import client


def test_rate_limit_per_sender_and_in_all():
    t = [0.0]
    rl = RateLimit(per_client=2, per_client_s=60, total=3, total_s=600, clock=lambda: t[0])
    assert rl.allow("a") is None and rl.allow("a") is None
    assert rl.allow("a") == "too_fast"
    assert rl.allow("b") is None
    assert rl.allow("c") == "busy"                     # three in ten minutes, whoever sends
    t[0] = 61
    assert rl.allow("a") == "busy"
    t[0] = 601
    assert rl.allow("a") is None


def test_a_live_message_goes_into_the_queue_and_its_sender_sees_only_it(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/clock", json={"action": "seek", "time": "2018-07-23T18:10:00+03:00"})
        info = c.get("/api/live").json()
        assert info["open"] and info["now"].startswith("2018-07-23T18:10")
        r = c.post("/api/live/send", json={"text": "Είμαστε 3 στην Κυκλάδων στο Μάτι, βλέπουμε φλόγες"})
        assert r.status_code == 200, r.text
        wid = r.json()["id"]
        assert wid == "w01"
        d = {}
        for _ in range(100):
            d = c.get(f"/api/live/{wid}").json()
            if d["read"]:
                break
            time.sleep(0.05)
        assert d["read"] and d["decision"] and not d["decided_by_person"]
        assert set(d) >= {"steps", "decision_el", "where", "status"} and "text" not in d   # no other data
        queue = c.get("/api/cases").json()["cases"]
        row = next(x for x in queue if x["id"] == wid)
        assert row["channel"] == "live_web"
        detail = c.get(f"/api/cases/{wid}").json()
        assert detail["entered_by"] is None                        # the public's own words, not an operator's
        assert c.get("/api/live/m01").status_code == 404           # a scenario message is not visible here
        assert c.get("/api/live/w99").status_code == 404
        assert c.post("/api/live/send", json={"text": "ab"}).status_code == 422
        codes = [c.post("/api/live/send", json={"text": f"Μήνυμα {i}"}).status_code for i in range(3)]
        assert codes[-1] == 429                                    # too fast from the same sender


def test_the_sender_answers_from_the_phone_only_for_their_own_message(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/clock", json={"action": "seek", "time": "2018-07-23T18:10:00+03:00"})
        r = c.post("/api/live/send", json={"text": "Βοήθεια, είμαστε παγιδευμένοι, φωτιά παντού"}).json()
        wid, token = r["id"], r["token"]
        for _ in range(100):
            if c.get(f"/api/live/{wid}").json()["read"]:
                break
            time.sleep(0.05)
        assert c.post(f"/api/live/{wid}/answer", json={"text": "Κυκλάδων 4", "token": "x" * 24}).status_code == 404
        a = c.post(f"/api/live/{wid}/answer", json={"text": "Κυκλάδων 4 στο Μάτι, είμαστε 2", "token": token})
        assert a.status_code == 200, a.text
        assert a.json()["answers"][0]["text"].startswith("Κυκλάδων 4")
        detail = c.get(f"/api/cases/{wid}").json()
        assert detail["replies"][-1]["by"] == "αποστολέας (κινητό)"


def test_when_the_operator_chooses_to_call_back_the_phone_gets_the_question(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/clock", json={"action": "seek", "time": "2018-07-23T18:10:00+03:00"})
        wid = c.post("/api/live/send", json={"text": "Βλέπω φωτιά τώρα"}).json()["id"]
        for _ in range(100):
            if c.get(f"/api/live/{wid}").json()["read"]:
                break
            time.sleep(0.05)
        r = c.post(f"/api/cases/{wid}/action", json={"action": "change", "decision": "CALLBACK",
                                                      "note": "Πού είσαι;", "operator": "anna"})
        assert r.status_code == 200, r.text
        assert c.get(f"/api/live/{wid}").json()["questions"] == ["Πού είσαι;"]


def test_a_person_writes_back_and_the_phone_shows_it_with_the_decision(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/clock", json={"action": "seek", "time": "2018-07-23T18:10:00+03:00"})
        wid = c.post("/api/live/send", json={"text": "Είμαστε καλά, φύγαμε, είμαστε στη Ραφήνα"}).json()["id"]
        for _ in range(100):
            if c.get(f"/api/live/{wid}").json()["read"]:
                break
            time.sleep(0.05)
        assert c.get(f"/api/live/{wid}").json()["advice_el"] is None          # nobody decided yet
        r = c.post(f"/api/cases/{wid}/to_sender",
                   json={"text": "Μείνετε στο λιμάνι, έρχεται πλοίο.", "operator": "anna"})
        assert r.status_code == 200, r.text
        v = c.get(f"/api/cases/{wid}").json()["version"]
        c.post(f"/api/cases/{wid}/to_sender", json={"text": "Δεύτερο μήνυμα", "operator": "anna"})
        assert c.get(f"/api/cases/{wid}").json()["version"] == v         # writing back does not make the case stale
        c.post(f"/api/cases/{wid}/action", json={"action": "change", "decision": "INFO", "operator": "anna"})
        s = c.get(f"/api/live/{wid}").json()
        assert s["from_centre"][0]["text"] == "Μείνετε στο λιμάνι, έρχεται πλοίο."
        assert s["advice_el"] == "Ευχαριστούμε για την πληροφορία."
        assert c.post("/api/cases/m01/to_sender", json={"text": "γεια σας"}).status_code == 409


def test_each_agent_shows_as_soon_as_it_ends(tmp_path):
    import threading
    from types import SimpleNamespace as NS

    from pydantic import BaseModel

    from palladion.live import progress
    from palladion.llm.client import LLM, ResponseCache, ScriptedProvider

    class Out(BaseModel):
        ok: bool

    llm = LLM(ScriptedProvider(lambda s, u, t: '{"ok": true}'), ResponseCache(tmp_path / "c.jsonl"), None,
              tmp_path / "t.jsonl")
    seen: list[str] = []
    with llm.watch(lambda r: seen.append(r.role)):
        llm.run("intake", "s", "u1", Out)
        other = threading.Thread(target=lambda: llm.run("needs", "s", "u2", Out))   # another thread: not seen
        other.start()
        other.join()
        llm.run("hazard_critic", "s", "u3", Out)
    llm.run("credibility", "s", "u4", Out)                                         # after: not seen
    assert seen == ["intake", "hazard_critic"]
    case = NS(progress=[{"role": "intake", "status": "ok", "seconds": 1.2},
                        {"role": "duplicates", "status": "ok", "seconds": 0.5},
                        {"role": "location", "status": "error", "seconds": 3.0}])
    assert [(s["agent"], s["status"]) for s in progress(case)] == [("intake", "ok"), ("location", "failed")]
