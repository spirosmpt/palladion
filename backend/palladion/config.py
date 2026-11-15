"""Runtime settings. Every value can be overridden with a PALLADION_* environment variable."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # .env is read from the current folder and from backend/, so it works whichever folder you start in.
    model_config = SettingsConfigDict(env_prefix="PALLADION_", env_file=(REPO_ROOT / "backend" / ".env", ".env"),
                                      extra="ignore")

    scenario_dir: Path = REPO_ROOT / "scenarios" / "mati_2018"
    data_dir: Path = REPO_ROOT / "data" / "processed"
    runtime_dir: Path = REPO_ROOT / "runtime"
    sample_mode: bool = True
    # LLM. "auto" = Gemini with an API key if one is set, else Vertex AI if a Google Cloud project is set,
    # else replay from the recorded cache only.
    llm_provider: Literal["auto", "gemini", "vertex", "replay"] = "auto"
    # Vertex AI: signs in with Application Default Credentials (`gcloud auth application-default login`), no key.
    gcp_project: str | None = None
    gcp_location: str = "global"
    vertex_model: str = "gemini-3.5-flash-lite"       # Gemini 2.5 was retired on Vertex AI (Oct 2026)
    vertex_model_strong: str = "gemini-3.8-flash"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-flash-lite-latest"      # default for every agent
    gemini_model_strong: str = "gemini-flash-latest"    # only when an agent asks for a second opinion
    llm_daily_limit: int = 450                          # stay under the free tier's requests per day
    llm_rpm: int = 10                                   # requests per minute
    # The observed burned area (Copernicus EMSR300) was published after the fire. With it, the reconstructed front is
    # confined to where it really burned; without it, the front stops after the last documented anchor.
    # Either way the front is a retrospective reconstruction.
    fire_use_perimeter: bool = True
    llm_ui_daily_limit: int = 200                       # the app's share; the rest is kept for evaluations
    llm_cache_dir: Path = REPO_ROOT / "data" / "llm_cache"
    fresh_start: bool = False       # archive the event log at every start (demo); False: decisions survive restarts
    supervisor_agent: bool = True   # the supervisor agent words the alerts; False: the findings' own text
    case_worker: bool = True        # follow the clock and triage arriving messages in the background
    live_inbox: bool = True         # the live page (/send): people write to the desk from a phone
    # Hosted (boxd): the desk opens only with this key (once, as /?key=...; then a cookie). /send stays open.
    room_key: str | None = None
    # Oumi Iris, a decision model, gives a second opinion on each reading (never decides). Off without a key.
    oumi_api_key: str | None = None
    oumi_project: str | None = None
    oumi_model: str = "iris-latest"
    # Browser origins allowed to call the API during development (Vite dev server).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
