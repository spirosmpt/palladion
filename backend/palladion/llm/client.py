"""LLM access for every agent: one place for providers, JSON validation, caching, budgets and traces.

An agent calls `LLM.run(role, system, user, OutputModel)` and gets back a validated Pydantic object
plus a trace record. The agent never sees which provider answered.

* Providers turn (system, user) into text. Gemini (API key) and Vertex AI (Google sign-in, ADC) are the
  real ones; Replay serves recorded answers.
* Every answer is cached by a hash of (model tier, system, user). The same prompt is never sent
  twice, and a demo can run entirely from the cache with no key and no network.
* A daily budget and a requests-per-minute spacing keep usage inside the free tier.
* Output must parse as JSON and validate against the agent's schema. One retry with the validation
  error; after that the call fails visibly (the pipeline then falls back to rules and says so).
"""
import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

Tier = Literal["default", "strong"]
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class BudgetExhausted(LLMError):
    pass


class CacheMiss(LLMError):
    pass


@dataclass
class CallRecord:
    role: str
    provider: str
    model: str
    tier: str
    key: str
    status: str                  # ok | cached | retry_ok | invalid | error | budget | refused
    seconds: float
    attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    original_seconds: float | None = None    # a cached answer: how long the real call took when it was made


class Provider(Protocol):
    name: str

    def model_for(self, tier: Tier) -> str: ...
    def generate(self, system: str, user: str, tier: Tier) -> tuple[str, dict[str, Any]]: ...


# -- providers ------------------------------------------------------------------------------------
class GeminiProvider:
    """Gemini REST API (generateContent) in JSON mode."""

    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    TRANSIENT = {500, 502, 503, 504}

    def __init__(self, api_key: str, model: str, strong_model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None, backoff: tuple[float, ...] = (2.0, 5.0),
                 sleep: Callable[[float], None] = time.sleep):
        self._key = api_key
        self._backoff, self._sleep = backoff, sleep
        self._models = {"default": model, "strong": strong_model}
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def model_for(self, tier: Tier) -> str:
        return self._models[tier]

    def generate(self, system: str, user: str, tier: Tier) -> tuple[str, dict[str, Any]]:
        model = self.model_for(tier)
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        r = self._post_with_retry(self._url(model), body)
        if r.status_code in self.TRANSIENT:
            raise LLMError(f"Gemini {model} is temporarily unavailable ({r.status_code}) after retries")
        if r.status_code == 429:
            raise BudgetExhausted(f"Gemini rate limit (429) for {model}: {_short(r.text)}")
        if r.status_code == 404:
            raise LLMError(f"model '{model}' not found; run `python -m palladion.llm.check` to list models")
        if r.status_code in (400, 401, 403):
            raise LLMError(f"Gemini refused the request ({r.status_code}): {_short(r.text)}")
        r.raise_for_status()
        data = r.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as exc:
            reason = data.get("promptFeedback", {}).get("blockReason") or data.get("candidates", [{}])[0].get(
                "finishReason")
            raise LLMError(f"Gemini returned no text (reason: {reason})") from exc
        usage = data.get("usageMetadata", {})
        return text, {"model": data.get("modelVersion", model),
                      "input_tokens": usage.get("promptTokenCount"),
                      "output_tokens": usage.get("candidatesTokenCount")}

    def _post_with_retry(self, url: str, body: dict[str, Any]) -> httpx.Response:
        """Overload (5xx) and timeouts are usually gone a few seconds later: retry, then give up visibly."""
        for wait in (*self._backoff, None):
            try:
                r = self._http.post(url, json=body, headers=self._headers())
            except httpx.TransportError as exc:
                if wait is None:
                    raise LLMError(f"Gemini unreachable: {exc}") from exc
                self._sleep(wait)
                continue
            if r.status_code not in self.TRANSIENT or wait is None:
                return r
            self._sleep(wait)
        raise AssertionError("unreachable")

    def _url(self, model: str) -> str:
        return f"{self.BASE}/models/{model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def list_models(self) -> list[str]:
        r = self._http.get(f"{self.BASE}/models", headers=self._headers(),
                           params={"pageSize": 200})
        r.raise_for_status()
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]


class VertexProvider(GeminiProvider):
    """Gemini on Vertex AI, signed in with Application Default Credentials (no API key).

    For organisations whose policy forbids API keys. Sign in once on the computer with
    `gcloud auth application-default login`; the token is taken from there and renewed when it expires.
    The request and the answer have the same shape as the Gemini API's, only the address and the sign-in differ.
    """

    name = "vertex"
    SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, project: str, location: str, model: str, strong_model: str,
                 token: Callable[[], str] | None = None, **kw: Any):
        super().__init__("", model, strong_model, **kw)
        self.project, self.location = project, location
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        self._host = f"https://{host}"
        self._base = f"{self._host}/v1/projects/{project}/locations/{location}/publishers/google/models"
        self._token = token or self._adc_token()

    def _adc_token(self) -> Callable[[], str]:
        try:
            import google.auth
            from google.auth.transport.requests import Request
        except ImportError as exc:
            raise LLMError("Vertex AI needs the google-auth package: pip install google-auth requests") from exc
        try:
            creds, _ = google.auth.default(scopes=[self.SCOPE])
        except Exception as exc:  # noqa: BLE001 - say what to do, not the stack
            raise LLMError("No Google sign-in found on this computer. Run: gcloud auth application-default login"
                           ) from exc
        lock = threading.Lock()

        def token() -> str:
            with lock:
                if not creds.valid:
                    try:
                        creds.refresh(Request())
                    except Exception as exc:  # noqa: BLE001
                        raise LLMError(f"Google sign-in could not be renewed: {_short(str(exc))}. "
                                       "Run: gcloud auth application-default login") from exc
                return str(creds.token)
        return token

    def _url(self, model: str) -> str:
        return f"{self._base}/{model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}", "x-goog-user-project": self.project}

    def list_models(self) -> list[str]:
        r = self._http.get(f"{self._host}/v1beta1/publishers/google/models", headers=self._headers(),
                           params={"pageSize": 300})
        r.raise_for_status()
        names = [m["name"].rsplit("/", 1)[-1] for m in r.json().get("publisherModels", [])]
        return sorted(n for n in names if n.startswith("gemini"))


class ReplayProvider:
    """Serves only what the cache holds. Used for demos, tests and when no key is configured."""

    name = "replay"

    def model_for(self, tier: Tier) -> str:
        return f"replay:{tier}"

    def generate(self, system: str, user: str, tier: Tier) -> tuple[str, dict[str, Any]]:
        raise CacheMiss("no recorded answer for this prompt (replay mode); set a Gemini key to record one")


class ScriptedProvider:
    """For tests: answers with a function of (system, user, tier)."""

    name = "scripted"

    def __init__(self, fn: Callable[[str, str, Tier], str]):
        self.fn = fn
        self.calls = 0

    def model_for(self, tier: Tier) -> str:
        return f"scripted:{tier}"

    def generate(self, system: str, user: str, tier: Tier) -> tuple[str, dict[str, Any]]:
        self.calls += 1
        return self.fn(system, user, tier), {}


# -- cache, budget, trace -------------------------------------------------------------------------
class ResponseCache:
    """Append-only JSONL. The key hashes the tier, not the model name, so a recorded demo replays
    after a model alias changes."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._mem: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._mem[row["key"]] = row

    @staticmethod
    def key(tier: str, system: str, user: str) -> str:
        return hashlib.sha256(f"{tier}\x00{system}\x00{user}".encode()).hexdigest()[:32]

    def get(self, key: str) -> dict[str, Any] | None:
        return self._mem.get(key)

    def put(self, key: str, row: dict[str, Any]) -> None:
        with self._lock:
            row = {"key": key, **row}
            self._mem[key] = row
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class Budget:
    """Requests per day (persisted) and minimum spacing between requests.

    One daily limit for everything that uses the key. A `pool` (the app's UI) also has its own share of it,
    so a busy session cannot spend what the evaluations need: the UI stops at `pool_limit`, the command-line
    evaluations can still use the rest. The file holds today's total and each pool's count."""

    def __init__(self, path: Path, daily_limit: int, rpm: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, pool: str | None = None, pool_limit: int | None = None):
        self.path, self.daily_limit = path, daily_limit
        self.pool, self.pool_limit = pool, pool_limit
        self.interval = 60.0 / max(1, rpm)
        self._clock, self._sleep = clock, sleep
        self._last = -1e9
        self._lock = threading.Lock()
        self.refused = 0          # calls refused because the day's budget was spent (this process)

    def _today(self) -> str:
        return datetime.now(UTC).date().isoformat()   # Gemini quotas reset at midnight Pacific; close enough

    def _read(self) -> dict[str, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        today = self._today()
        return {k: int(v) for k, v in data.items() if k == today or k.startswith(today + "/")}

    def used_today(self, pool: str | None = None) -> int:
        return self._read().get(self._today() + (f"/{pool}" if pool else ""), 0)

    def status(self) -> dict[str, int | None]:
        data, today = self._read(), self._today()
        return {"used": data.get(today, 0), "limit": self.daily_limit,
                "pool_used": data.get(f"{today}/{self.pool}", 0) if self.pool else None,
                "pool_limit": self.pool_limit if self.pool else None}

    def take(self) -> None:
        with self._lock:
            data, today = self._read(), self._today()
            used = data.get(today, 0)
            if used >= self.daily_limit:
                self.refused += 1
                raise BudgetExhausted(f"daily LLM budget reached ({used}/{self.daily_limit})")
            key = f"{today}/{self.pool}" if self.pool else None
            if key and self.pool_limit is not None and data.get(key, 0) >= self.pool_limit:
                self.refused += 1
                raise BudgetExhausted(f"the {self.pool}'s share of the daily LLM budget reached "
                                      f"({data[key]}/{self.pool_limit}); the rest is kept for evaluations")
            wait = self._last + self.interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            data[today] = used + 1
            if key:
                data[key] = data.get(key, 0) + 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")


# Answers the provider's safety filter gives for this exact prompt; the same prompt gets the same answer.
REFUSALS = ("PROHIBITED_CONTENT", "reason: SAFETY", "reason: BLOCKLIST", "reason: SPII")


# -- the runner agents use ------------------------------------------------------------------------
JSON_RULES = (
    "Answer with one JSON object only, no markdown fences, matching this JSON Schema exactly. "
    "Use null when the message does not say something; never guess.\n"
)


class LLM:
    def __init__(self, provider: Provider, cache: ResponseCache, budget: Budget | None, trace_path: Path):
        self.provider, self.cache, self.budget = provider, cache, budget
        self.trace_path = trace_path
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._durations: dict[str, float] | None = None

    def _original_seconds(self, key: str, hit: dict[str, Any]) -> float | None:
        """How long the real call behind a cached answer took: stored with the answer since 13/11; for older
        answers, looked up once in the call log (the last successful real call with the same key)."""
        if isinstance(hit.get("seconds"), (int, float)):
            return float(hit["seconds"])
        if self._durations is None:
            self._durations = {}
            if self.trace_path.exists():
                with self.trace_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("status") in ("ok", "retry_ok") and r.get("key"):
                            self._durations[r["key"]] = float(r.get("seconds") or 0.0)
        return self._durations.get(key)

    _watch = threading.local()

    @contextmanager
    def watch(self, fn: Callable[[CallRecord], None]) -> Iterator[None]:
        """Every call made on this thread meanwhile is also told to `fn` as soon as it ends (for live progress)."""
        prev = getattr(self._watch, "fn", None)
        self._watch.fn = fn
        try:
            yield
        finally:
            self._watch.fn = prev

    def _trace(self, rec: CallRecord) -> None:
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        fn = getattr(self._watch, "fn", None)
        if fn is not None:
            try:
                fn(rec)
            except Exception:  # noqa: BLE001, S110 - a watcher must never break a call
                pass

    def run(self, role: str, system: str, user: str, schema: type[T], tier: Tier = "default") -> tuple[T, CallRecord]:
        full_system = system + "\n\n" + JSON_RULES + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        key = ResponseCache.key(tier, full_system, user)
        t0 = time.monotonic()
        hit = self.cache.get(key)
        if hit is not None and "refused" in hit:
            # The provider's safety filter refused this exact prompt before; it would again. Do not spend a call.
            self._trace(CallRecord(role, hit.get("provider", "cache"), hit.get("model", ""), tier, key, "refused",
                                   time.monotonic() - t0, 0, error=hit["refused"]))
            raise LLMError(f"{hit['refused']} (refused before; not sent again)")
        if hit is not None:
            try:
                out = schema.model_validate_json(hit["text"])
                rec = CallRecord(role, hit.get("provider", "cache"), hit.get("model", ""), tier, key, "cached",
                                 time.monotonic() - t0, 0, original_seconds=self._original_seconds(key, hit))
                self._trace(rec)
                return out, rec
            except ValidationError:
                pass   # schema changed since recording: ask again

        attempts, prompt, last_err = 0, user, ""
        meta: dict[str, Any] = {}
        while attempts < 2:
            attempts += 1
            try:
                if self.budget is not None and not isinstance(self.provider, ReplayProvider):
                    self.budget.take()
                text, meta = self.provider.generate(full_system, prompt, tier)
            except LLMError as exc:
                rec = CallRecord(role, self.provider.name, self.provider.model_for(tier), tier, key,
                                 "budget" if isinstance(exc, BudgetExhausted) else "error",
                                 time.monotonic() - t0, attempts, error=str(exc))
                self._trace(rec)
                if any(w in str(exc) for w in REFUSALS):
                    self.cache.put(key, {"provider": self.provider.name, "role": role, "refused": str(exc)})
                raise
            try:
                out = schema.model_validate_json(_strip_fences(text))
            except ValidationError as exc:
                last_err = _short(str(exc), 600)
                prompt = (f"{user}\n\nYour previous answer was not valid for the schema:\n{last_err}\n"
                          f"Previous answer:\n{_short(text, 1500)}\nReturn a corrected JSON object.")
                continue
            took = round(time.monotonic() - t0, 2)
            self.cache.put(key, {"provider": self.provider.name, "model": meta.get("model", ""),
                                 "role": role, "text": out.model_dump_json(), "seconds": took})
            rec = CallRecord(role, self.provider.name, meta.get("model", self.provider.model_for(tier)), tier, key,
                             "ok" if attempts == 1 else "retry_ok", time.monotonic() - t0, attempts,
                             meta.get("input_tokens"), meta.get("output_tokens"))
            self._trace(rec)
            return out, rec
        rec = CallRecord(role, self.provider.name, self.provider.model_for(tier), tier, key, "invalid",
                         time.monotonic() - t0, attempts, error=last_err)
        self._trace(rec)
        raise LLMError(f"{role}: model output did not match the schema after a retry: {last_err}")


def build_llm(settings, scenario_id: str, pool: str | None = None) -> LLM:
    """`pool="ui"`: the running app, limited to its share of the day (settings.llm_ui_daily_limit)."""
    provider: Provider
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    if settings.llm_provider == "vertex" or (settings.llm_provider == "auto" and not key and settings.gcp_project):
        if not settings.gcp_project:
            raise LLMError("PALLADION_GCP_PROJECT is not set (backend/.env)")
        provider = VertexProvider(settings.gcp_project, settings.gcp_location, settings.vertex_model,
                                  settings.vertex_model_strong)
    elif settings.llm_provider == "gemini" or (settings.llm_provider == "auto" and key):
        if not key:
            raise LLMError("PALLADION_GEMINI_API_KEY is not set (backend/.env)")
        provider = GeminiProvider(key, settings.gemini_model, settings.gemini_model_strong)
    else:
        provider = ReplayProvider()
    cache = ResponseCache(settings.llm_cache_dir / f"{scenario_id}.jsonl")
    budget = Budget(settings.runtime_dir / "llm_budget.json", settings.llm_daily_limit, settings.llm_rpm,
                    pool=pool, pool_limit=settings.llm_ui_daily_limit if pool == "ui" else None)
    return LLM(provider, cache, budget, settings.runtime_dir / "logs" / "llm_calls.jsonl")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _short(s: str, n: int = 300) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
