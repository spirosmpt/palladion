"""Check the model setup:  python -m palladion.llm.check

Works with either sign-in:
  - an API key (PALLADION_GEMINI_API_KEY in backend/.env), or
  - Vertex AI with your Google sign-in (PALLADION_GCP_PROJECT in backend/.env, after
    `gcloud auth application-default login`), for organisations that do not allow API keys.
Lists the Gemini models you can use, sends one tiny request to each configured model, and shows how much of
today's budget is used. No key or token is ever printed.
"""
import sys

from pydantic import BaseModel

from palladion.config import get_settings
from palladion.llm.client import Budget, GeminiProvider, LLMError, VertexProvider


class Ping(BaseModel):
    ok: bool
    language: str


def provider(s):  # noqa: ANN001, ANN201
    key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
    if s.llm_provider == "vertex" or (s.llm_provider != "gemini" and not key and s.gcp_project):
        if not s.gcp_project:
            print("PALLADION_GCP_PROJECT is missing. Put your Google Cloud project id in backend/.env.")
            return None, None
        print(f"Vertex AI: project {s.gcp_project}, location {s.gcp_location} (Google sign-in, no key).")
        return VertexProvider(s.gcp_project, s.gcp_location, s.vertex_model, s.vertex_model_strong, timeout=30), \
            ("PALLADION_VERTEX_MODEL", "PALLADION_VERTEX_MODEL_STRONG")
    if not key:
        print("No sign-in configured. In backend/.env set either PALLADION_GEMINI_API_KEY, or "
              "PALLADION_GCP_PROJECT (Vertex AI, after `gcloud auth application-default login`).")
        return None, None
    print(f"Gemini API key found ({len(key)} characters).")
    return GeminiProvider(key, s.gemini_model, s.gemini_model_strong, timeout=30), \
        ("PALLADION_GEMINI_MODEL", "PALLADION_GEMINI_MODEL_STRONG")


def main() -> int:
    s = get_settings()
    try:
        p, names = provider(s)
    except LLMError as exc:
        print(exc)
        return 1
    if p is None or names is None:
        return 1
    try:
        models = p.list_models()
        flash = sorted(m for m in models if "flash" in m)
        print(f"{len(models)} models available; flash models: {', '.join(flash) or '-'}")
    except Exception as exc:  # noqa: BLE001 - listing is a help, not a requirement
        print(f"Could not list models ({type(exc).__name__}); trying the configured ones anyway.")
    status = 0
    for tier in ("default", "strong"):
        model = p.model_for(tier)  # type: ignore[arg-type]
        try:
            text, meta = p.generate(
                "Reply in JSON: {\"ok\": true, \"language\": \"<language of the user text>\"}",
                "Καίγεται το δάσος πάνω από το σπίτι μας", tier)  # type: ignore[arg-type]
            Ping.model_validate_json(text)
            print(f"  {tier:7s} {model}: OK ({meta.get('model')}, {meta.get('input_tokens')} tokens in)")
        except (LLMError, Exception) as exc:  # noqa: BLE001
            print(f"  {tier:7s} {model}: FAILED: {exc}")
            status = 1
    b = Budget(s.runtime_dir / "llm_budget.json", s.llm_daily_limit, s.llm_rpm)
    print(f"Palladion budget today: {b.used_today()}/{s.llm_daily_limit} requests "
          "(this check is not counted).")
    if status:
        print(f"If a model is not found, pick one from the list above and set {names[0]} "
              f"or {names[1]} in backend/.env.")
    return status


if __name__ == "__main__":
    sys.exit(main())
