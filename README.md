# CodeMind OS

AI-powered repository intelligence for developers: index a local Git repository, ask evidence-backed questions, trace change impact, review quality, plan tests and draft documentation — **entirely on your machine** (local LLM via Ollama, local embeddings, local storage, no telemetry, no paid APIs).

Guiding principle: **evidence before automation.** Every answer separates *verified repository facts* (with `file:line` citations that are mechanically checked) from *interpretations* and *recommendations*. If the evidence is insufficient, CodeMind OS says so instead of guessing.

> Read `docs/IMPLEMENTATION_STATUS.md` first: it lists exactly what has been tested and what has not.

## What works without any model

Indexing, the knowledge graph, exact/lexical/semantic(hash)/graph retrieval, citation verification, rule-based quality review, change-impact analysis, test planning and documentation generation all work **without Ollama**. A local LLM adds natural-language interpretation on top; when it is missing or fails, results say so (`ai.used = false`, `ai.error`) and fall back to the deterministic output.

## Quick start

### Linux / macOS
```bash
git clone <this repo> CodeMind_OS && cd CodeMind_OS
bash scripts/setup_linux.sh            # venv + dependencies; downloads no models
source .venv/bin/activate
python scripts/check_environment.py    # health check
python main.py serve                   # web UI + API on http://127.0.0.1:8000/
```

### Windows (PowerShell)
```powershell
# 1. Install Python 3.11+ from https://www.python.org/downloads/ (tick "Add to PATH")
git clone <this repo> CodeMind_OS; cd CodeMind_OS
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1   # venv + dependencies
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe main.py serve                         # open http://127.0.0.1:8000/
```
Always run through the venv's Python (`.\.venv\Scripts\python.exe -m ...`) so a stale global launcher on PATH cannot interfere.

Then in the browser: **create the administrator account** (the first sign-up on a fresh install), then **Repositories → add a folder path → Index → Ask / Quality / Impact / Tests / Docs**. Other people can sign up too; each user only sees their own repositories, and the administrator manages users on the **Administration** page. The sample project is `sample_repo/`.

CLI (no browser needed): `python main.py index sample_repo` then `python main.py ask <repo_id> "How is the low stock notification sent?" --no-llm`.

### Deploy on Render
See **`docs/DEPLOY_RENDER.md`** (Docker + `render.yaml` blueprint, account-protected hosted mode with admin approval, free-tier limits and the privacy trade-offs of hosting).

### Local LLM (optional, your choice)
1. Install [Ollama](https://ollama.com) separately and start it.
2. Pull a model **yourself** (nothing is downloaded automatically): `ollama pull qwen2.5-coder:7b`
   - ~4.7 GB download; plan for ≥8 GB free RAM (16 GB comfortable), GPU optional. Check the model card for its licence (the 7B Qwen2.5-Coder model is published under Apache-2.0; other sizes differ).
3. Change the model with `CODEMIND_OLLAMA_MODEL` or on the dashboard's **Settings & privacy** page. It must already be installed.

### Neural embeddings (optional)
`python scripts/check_environment.py --download-embedding-model` downloads `all-MiniLM-L6-v2` (~90 MB, Apache-2.0) after you confirm. Afterwards embeddings run fully offline. Without it, a deterministic hashing embedder is used and **clearly labelled as non-semantic**.

### Tests
```bash
pip install -r requirements-dev.txt
python -m pytest                        # unit + synthetic-repo tests; no LLM needed (UI smoke test needs Node.js)
pytest tests/test_ollama_integration.py # optional, needs a running Ollama model
python scripts/evaluate.py              # reproducible retrieval/impact evaluation
ruff check . && mypy .                  # code quality (see status doc)
```

## Configuration
All settings are `CODEMIND_*` environment variables (see `.env.example`). Storage defaults to `~/.codemind_os` (database, vectors, graphs, reports, model cache). Generated reports and test proposals are written there, never into your repository, unless you enable and confirm repository writes.

## Safety defaults
Read-only indexing · repository code is never executed · packages are never installed · secrets/credential files/binaries/dependency dirs/symlinks excluded · repository text is treated as untrusted data in prompts · writes and test execution are disabled and need both an environment switch and per-action approval · API binds to 127.0.0.1 · Ollama host must be loopback · user accounts with roles (admin/user), per-user isolation and an audit log; hosted mode additionally requires an administrator and approves sign-ups. Details: `docs/SECURITY.md`.

## Project layout
```
main.py (CLI + server) · web/ (UI: HTML/CSS/JS, no build step, no external assets) · config/ · backend/ (FastAPI) · ingestion/ · parsing/ · graph/
retrieval/ · agents/ (LangGraph workflow + specialists) · verification/ · services/ · evaluation/ · scripts/
tests/ · docs/ · sample_repo/ (synthetic demo) · examples/reports/ (real outputs on the sample)
```

## Troubleshooting
See `docs/USER_GUIDE.md#troubleshooting`. Common: *Ollama not reachable* → start Ollama; *model missing* → `ollama pull <model>`; *"index is stale"* → click **Update index**; *Chroma errors* → set `CODEMIND_VECTOR_BACKEND=sqlite` or run a full re-index; *Windows execution policy* → use the `-ExecutionPolicy Bypass` command above; *`streamlit`/`pytest` launcher errors* → use `.venv\Scripts\python.exe -m …`.

## Licence
MIT (see `LICENSE`). Third-party packages and model weights keep their own licences.
