from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"

load_dotenv(PROJECT_ROOT / ".env")


def _env_first(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


GEMINI_API_KEY: str = _env_first("GEMINI_API_KEY", "GOOGLE_API_KEY", default="")
OPENAI_API_KEY: str = _env_first("OPENAI_API_KEY", default="")
OPENAI_BASE_URL: str = _env_first("OPENAI_BASE_URL", default="")

MODEL_PROVIDER: str = _env_first("MODEL_PROVIDER", default="gemini")
VLM_MODEL: str = _env_first("VLM_MODEL", "GEMINI_VLM_MODEL", default="gemini-2.5-flash")
LLM_MODEL: str = _env_first("LLM_MODEL", "GEMINI_LLM_MODEL", default="gemini-2.5-flash")
MODEL_GROUP: str = _env_first("MODEL_GROUP", default="")

DECISION_MODEL_ENABLED: bool = _env_first("DECISION_MODEL_ENABLED", default="0").lower() in {"1", "true", "yes", "on"}
DECISION_MODEL: str = _env_first("DECISION_MODEL", default="gpt-5.1-chat")
DECISION_MODEL_GROUP: str = _env_first("DECISION_MODEL_GROUP", default="")
DECISION_MODEL_FALLBACK: str = _env_first("DECISION_MODEL_FALLBACK", default="gpt-5-mini")
DECISION_MODEL_FALLBACK_GROUP: str = _env_first("DECISION_MODEL_FALLBACK_GROUP", default="")
DECISION_MODEL_EXPERT: str = _env_first("DECISION_MODEL_EXPERT", default="gpt-5-pro")
DECISION_MODEL_EXPERT_GROUP: str = _env_first("DECISION_MODEL_EXPERT_GROUP", default="")
DECISION_SCORE_TRIGGER: float = float(_env_first("DECISION_SCORE_TRIGGER", default="0.55"))
DECISION_MIN_ITERATION: int = int(_env_first("DECISION_MIN_ITERATION", default="2"))

MAX_EVAL_ITERATIONS: int = int(_env_first("MAX_EVAL_ITERATIONS", default="3"))
QUALITY_THRESHOLD: float = float(_env_first("QUALITY_THRESHOLD", default="0.75"))

