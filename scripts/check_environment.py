"""Environment health check: dependencies, Ollama/model, storage permissions, disk space.

  python scripts/check_environment.py
  python scripts/check_environment.py --download-embedding-model     (explicit, asks for confirmation)
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import load_settings  # noqa: E402

REQUIRED = ["fastapi", "uvicorn", "pydantic", "networkx", "numpy", "yaml", "requests"]
OPTIONAL = {"chromadb": "vector store (falls back to SQLite+NumPy)", "sentence_transformers": "neural embeddings (falls back to hashing)",
            "langgraph": "LangGraph engine (falls back to a sequential engine)", "tree_sitter": "exact multi-language parsing (falls back to regex)",
            "pytest": "running the tests", "httpx": "API tests"}


def line(ok: bool | None, msg: str) -> None:
    print(("[ OK ] " if ok else "[WARN] " if ok is None else "[FAIL] ") + msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-embedding-model", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    bad = 0
    v = sys.version_info
    line(v >= (3, 11), f"Python {v.major}.{v.minor}.{v.micro} (3.11+ required)")
    bad += v < (3, 11)
    for m in REQUIRED:
        try:
            importlib.import_module(m); line(True, f"required package '{m}'")
        except ImportError:
            line(False, f"required package '{m}' missing -> pip install -r requirements.txt"); bad += 1
    for m, why in OPTIONAL.items():
        try:
            importlib.import_module(m); line(True, f"optional package '{m}'")
        except ImportError:
            line(None, f"optional package '{m}' missing - {why}")
    git = shutil.which("git")
    line(bool(git), "git found" if git else "git not found (revision info will be 'nogit')")
    try:
        s.ensure_dirs()
        with tempfile.NamedTemporaryFile(dir=s.storage_dir):
            pass
        line(True, f"storage directory writable: {s.storage_dir}")
        free = shutil.disk_usage(s.storage_dir).free // (1024 * 1024)
        line(free >= s.min_free_disk_mb, f"free disk space: {free} MB (minimum {s.min_free_disk_mb} MB)")
        bad += free < s.min_free_disk_mb
    except OSError as e:
        line(False, f"storage directory not writable: {e}"); bad += 1
    try:
        from agents.llm_client import OllamaClient
        h = OllamaClient(s).health()
        if h["available"]:
            line(h["model_installed"], f"Ollama reachable; model '{h['model']}' " + ("installed" if h["model_installed"] else f"NOT installed -> run: ollama pull {h['model']}"))
        else:
            line(None, h["error"])
    except Exception as e:  # noqa: BLE001
        line(None, f"Ollama check skipped: {e}")
    try:
        from retrieval.embeddings import get_embedder
        e = get_embedder(s)
        line(True if e.is_semantic else None, f"embedding backend in use: {e.name}" + ("" if e.is_semantic else f" (fallback: {e.fallback_reason or 'configured'})"))
    except Exception as ex:  # noqa: BLE001
        line(False, f"embedding backend failed: {ex}")
    if a.download_embedding_model:
        print(f"\nThis will download '{s.embedding_model}' (~90 MB) from Hugging Face into {s.models_dir}.")
        if input("Type 'yes' to continue: ").strip().lower() == "yes":
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            from sentence_transformers import SentenceTransformer
            SentenceTransformer(s.embedding_model, cache_folder=str(s.models_dir))
            print("Downloaded. Embeddings now work fully offline.")
        else:
            print("Skipped.")
    print("\nSummary:", "problems found" if bad else "no blocking problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
