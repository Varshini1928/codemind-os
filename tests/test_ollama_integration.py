"""Optional: needs a running Ollama with the configured model. Skipped otherwise."""
import pytest

from agents.llm_client import OllamaClient
from config.settings import load_settings


def _available():
    try:
        h = OllamaClient(load_settings()).health()
        return h["available"] and h["model_installed"]
    except Exception:
        return False


@pytest.mark.skipif(not _available(), reason="Ollama or the configured model is not available")
def test_real_model_answers_with_verified_citations(indexed, monkeypatch):
    svc, rid, _ = indexed
    res = svc.analysis.ask(rid, "How is a discount applied to a product price?", save=False)
    assert res["ai"]["used"] and res["verification"]["total"] > 0
