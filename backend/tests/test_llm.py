import json

import httpx
import pytest
from pydantic import BaseModel

from palladion.llm.client import (
    LLM,
    Budget,
    BudgetExhausted,
    CacheMiss,
    GeminiProvider,
    LLMError,
    ReplayProvider,
    ResponseCache,
    ScriptedProvider,
)


class Out(BaseModel):
    n: int


def make(tmp_path, provider, budget=None):
    return LLM(provider, ResponseCache(tmp_path / "cache.jsonl"), budget, tmp_path / "trace.jsonl")


def test_valid_answer_is_cached_and_replayed(tmp_path):
    p = ScriptedProvider(lambda s, u, t: '{"n": 3}')
    llm = make(tmp_path, p)
    out, rec = llm.run("intake", "sys", "msg", Out)
    assert out.n == 3 and rec.status == "ok" and p.calls == 1
    out2, rec2 = llm.run("intake", "sys", "msg", Out)
    assert out2.n == 3 and rec2.status == "cached" and p.calls == 1
    # A fresh process in replay mode serves the same answer without any provider.
    out3, _ = make(tmp_path, ReplayProvider()).run("intake", "sys", "msg", Out)
    assert out3.n == 3


def test_invalid_json_gets_one_retry_then_fails_visibly(tmp_path):
    answers = iter(['not json', '{"n": 5}'])
    llm = make(tmp_path, ScriptedProvider(lambda s, u, t: next(answers)))
    out, rec = llm.run("intake", "sys", "msg", Out)
    assert out.n == 5 and rec.status == "retry_ok" and rec.attempts == 2

    llm2 = make(tmp_path / "b", ScriptedProvider(lambda s, u, t: '{"n": "x"}'))
    with pytest.raises(LLMError, match="did not match"):
        llm2.run("intake", "sys", "other", Out)
    trace = [json.loads(line) for line in (tmp_path / "b" / "trace.jsonl").read_text().splitlines()]
    assert trace[-1]["status"] == "invalid"


def test_code_fences_are_tolerated(tmp_path):
    llm = make(tmp_path, ScriptedProvider(lambda s, u, t: '```json\n{"n": 1}\n```'))
    assert llm.run("r", "s", "u", Out)[0].n == 1


def test_replay_without_recording_is_a_cache_miss(tmp_path):
    with pytest.raises(CacheMiss):
        make(tmp_path, ReplayProvider()).run("r", "s", "u", Out)


def test_daily_budget_and_spacing(tmp_path):
    t = [0.0]
    slept = []
    b = Budget(tmp_path / "b.json", daily_limit=2, rpm=30, clock=lambda: t[0], sleep=lambda s: slept.append(s))
    llm = make(tmp_path, ScriptedProvider(lambda s, u, tier: '{"n": 1}'), b)
    llm.run("r", "s", "a", Out)
    llm.run("r", "s", "b", Out)
    assert slept and abs(slept[0] - 2.0) < 1e-9          # 30 rpm -> 2 s between requests
    with pytest.raises(BudgetExhausted):
        llm.run("r", "s", "c", Out)
    assert llm.run("r", "s", "a", Out)[1].status == "cached"   # cached answers cost nothing


def test_gemini_request_shape_and_errors():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        if "missing-model" in str(req.url):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if "busy-model" in str(req.url):
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"n": 7}'}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3},
            "modelVersion": "flash-lite-x"})

    p = GeminiProvider("k-123", "flash-lite", "missing-model", transport=httpx.MockTransport(handler))
    text, meta = p.generate("system text", "user text", "default")
    assert json.loads(text) == {"n": 7} and meta["input_tokens"] == 11 and meta["model"] == "flash-lite-x"
    assert seen["url"].endswith("/models/flash-lite:generateContent") and "key=" not in seen["url"]
    assert seen["key"] == "k-123"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "system text"
    with pytest.raises(LLMError, match="not found"):
        p.generate("s", "u", "strong")
    busy = GeminiProvider("k", "busy-model", "busy-model", transport=httpx.MockTransport(handler))
    with pytest.raises(BudgetExhausted):
        busy.generate("s", "u", "default")


def test_gemini_retries_temporary_overload():
    codes = iter([503, 503, 200])

    def handler(req):
        c = next(codes)
        if c != 200:
            return httpx.Response(c, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"n": 1}'}]}}]})

    waits = []
    p = GeminiProvider("k", "m", "m", transport=httpx.MockTransport(handler), sleep=waits.append)
    text, _ = p.generate("s", "u", "default")
    assert json.loads(text) == {"n": 1} and waits == [2.0, 5.0]

    always = GeminiProvider("k", "m", "m", transport=httpx.MockTransport(
        lambda r: httpx.Response(503, json={})), sleep=lambda s: None)
    with pytest.raises(LLMError, match="temporarily unavailable"):
        always.generate("s", "u", "default")


def test_a_safety_refusal_is_remembered_and_not_sent_again(tmp_path):
    class Refuses(ScriptedProvider):
        def generate(self, system, user, tier):
            self.calls += 1
            raise LLMError("Gemini returned no text (reason: PROHIBITED_CONTENT)")
    p = Refuses(lambda s, u, t: "")
    budget = Budget(tmp_path / "b.json", 10, 6000, sleep=lambda s: None)
    llm = make(tmp_path, p, budget)
    for _ in range(3):
        with pytest.raises(LLMError, match="PROHIBITED_CONTENT"):
            llm.run("duplicates", "sys", "msg", Out)
    assert p.calls == 1 and budget.used_today() == 1
    # A temporary failure is not remembered.
    flaky = ScriptedProvider(lambda s, u, t: '{"n": 1}')
    assert make(tmp_path, flaky).run("x", "sys", "other", Out)[0].n == 1


def test_budget_counts_refused_calls(tmp_path):
    budget = Budget(tmp_path / "b.json", 1, 6000, sleep=lambda s: None)
    budget.take()
    with pytest.raises(BudgetExhausted):
        budget.take()
    assert budget.refused == 1


def test_the_app_has_its_share_and_evaluations_keep_the_rest(tmp_path):
    path = tmp_path / "b.json"
    ui = Budget(path, 5, 6000, sleep=lambda s: None, pool="ui", pool_limit=2)
    cli = Budget(path, 5, 6000, sleep=lambda s: None)
    ui.take()
    ui.take()
    with pytest.raises(BudgetExhausted, match="kept for evaluations"):
        ui.take()
    assert ui.status() == {"used": 2, "limit": 5, "pool_used": 2, "pool_limit": 2}
    cli.take()
    cli.take()
    cli.take()                                            # evaluations use the rest of the day
    with pytest.raises(BudgetExhausted, match="daily LLM budget reached"):
        cli.take()
    assert cli.used_today() == 5 and cli.used_today("ui") == 2


def test_a_cached_answer_keeps_how_long_the_real_call_took(tmp_path):
    import time as _t

    def slow(s, u, t):
        _t.sleep(0.05)
        return '{"n": 1}'
    llm = make(tmp_path, ScriptedProvider(slow))
    _, rec = llm.run("intake", "sys", "a", Out)
    _, again = make(tmp_path, ReplayProvider()).run("intake", "sys", "a", Out)
    assert again.status == "cached" and again.original_seconds is not None
    assert abs(again.original_seconds - rec.seconds) < 0.02
    # An answer cached before 13/11 has no time stored: it is found in the call log by its key.
    cache = ResponseCache(tmp_path / "old.jsonl")
    key = ResponseCache.key("default", "x", "y")
    cache.put(key, {"provider": "gemini", "model": "m", "role": "intake", "text": '{"n": 2}'})
    with (tmp_path / "trace.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "status": "ok", "seconds": 3.25}) + "\n")
    old = LLM(ReplayProvider(), cache, None, tmp_path / "trace.jsonl")
    assert old._original_seconds(key, {"text": "{}"}) == 3.25


def test_vertex_signs_in_with_a_token_not_a_key():
    from palladion.llm.client import VertexProvider
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        seen["key"], seen["project"] = req.headers.get("x-goog-api-key"), req.headers.get("x-goog-user-project")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"n": 2}'}]}}],
                                         "modelVersion": "gemini-x"})

    tokens = iter(["t1", "t2"])
    p = VertexProvider("my-proj", "global", "lite", "strong", token=lambda: next(tokens),
                       transport=httpx.MockTransport(handler))
    text, meta = p.generate("s", "u", "default")
    assert json.loads(text) == {"n": 2} and meta["model"] == "gemini-x" and p.name == "vertex"
    assert seen["url"] == ("https://aiplatform.googleapis.com/v1/projects/my-proj/locations/global/"
                           "publishers/google/models/lite:generateContent")
    assert seen["auth"] == "Bearer t1" and seen["key"] is None and seen["project"] == "my-proj"
    p.generate("s", "u", "strong")
    assert seen["auth"] == "Bearer t2" and seen["url"].endswith("/models/strong:generateContent")
    eu = VertexProvider("p", "europe-west4", "m", "m", token=lambda: "t", transport=httpx.MockTransport(handler))
    eu.generate("s", "u", "default")
    assert seen["url"].startswith("https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4/")


def test_build_llm_picks_vertex_when_a_project_is_set_and_no_key(tmp_path, monkeypatch):
    from palladion.config import Settings
    from palladion.llm import client as C
    monkeypatch.setattr(C.VertexProvider, "_adc_token", lambda self: (lambda: "t"))
    s = Settings(runtime_dir=tmp_path, llm_cache_dir=tmp_path, gemini_api_key=None, gcp_project="proj",
                 llm_provider="auto", _env_file=None)
    assert C.build_llm(s, "x").provider.name == "vertex"
    s2 = Settings(runtime_dir=tmp_path, llm_cache_dir=tmp_path, gemini_api_key=None, gcp_project=None,
                  llm_provider="auto", _env_file=None)
    assert C.build_llm(s2, "x").provider.name == "replay"
