import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

LLM_ENV = ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_TEMPERATURE",
           "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL")


@pytest.fixture(autouse=True)
def _no_real_llm_env(monkeypatch):
    """No test may see the developer's real .env or keys: every CLI run would otherwise load them and could call the real API."""
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("forecaster.cli.load_env", lambda *a, **k: None)
