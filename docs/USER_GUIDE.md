# User guide

Start the server (`python main.py serve`) and open **http://127.0.0.1:8000/**. You will see the **Sign in** page. On a fresh local install click *Create the administrator account* — the first account becomes the administrator. Everyone else clicks *Create an account* (on a hosted server an administrator may need to approve it first).

| Page | What it does |
|---|---|
| **Overview** | Index health, languages, entities, entry points, endpoints, configuration keys, external dependencies, excluded-file counts, parse reliability. |
| **Repositories** | Add a local folder (local mode) or a public Git URL, index / update / full re-index, watch live progress and logs, cancel, delete the index, remove the repository. |
| **Explorer** | Browse indexed files (with parse status), view source, search symbols. Excluded files (secrets, binaries, dependencies) never appear. |
| **Knowledge graph** | Pick a symbol/file, depth, direction and edge types; solid green = statically resolved, dashed amber = heuristic. Click nodes for details, scroll to zoom, drag to pan, export JSON. |
| **Ask** | Evidence-grounded Q&A; multi-part questions are split. Facts show *citation verified*; claims without valid evidence show *unverified hypothesis*. Click an `E1` chip to see the cited lines. |
| **Code quality** | Candidate findings with severity, category, uncertainty and next step; filter and search. These are candidates, not confirmed defects. |
| **Change impact** | Dependents (direct/transitive), related tests, configuration, endpoints, docs to review, unresolved possible callers, with a confidence label on every link. |
| **Test planner** | Evidence-linked test specifications (behaviour, inputs, expected result and its basis, boundaries, exceptions). Download as Markdown. No test code is generated and nothing is executed. |
| **Documentation** | Markdown generated from extracted facts with `file:line` references; "Verified repository facts" are separated from "Suggested improvements". |
| **Reports** | Every analysis is saved automatically as Markdown + JSON; open, download or delete. |
| **My account** | Your profile, change password, sign out other devices, your repositories, usage numbers and recent activity (sign-ins, password changes…). If an administrator reset your password you land here and must choose a new one. |
| **Administration** (admins only) | *Overview* (users, repositories, storage, jobs), *Users* (approve pending accounts, disable/enable, make admin/user, reset password, delete), *Repositories* (everyone's, with owners), *Audit log*. |
| **Settings & privacy** | System status, safety switches (read-only), model name, ranking weights, storage locations, sign out. |

The top bar shows the selected repository, its index status and whether the local AI model is online. When AI is offline every analysis still works in deterministic mode and the result says so. CLI equivalents: `python main.py index|ask|review|impact|tests|docs|report|repos|health` (`--no-llm`, `--json`).

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Ollama is not reachable" | Install/start Ollama (`ollama serve`); check `CODEMIND_OLLAMA_HOST`. Everything except AI interpretation still works. |
| "Model … is not installed" | `ollama pull <model>` yourself, or set `CODEMIND_OLLAMA_MODEL` to an installed model. |
| Semantic results look weak / banner says hashing fallback | Install `sentence-transformers` and run `python scripts/check_environment.py --download-embedding-model`, then **Full re-index**. |
| "Index was built with X but Y is configured" | Embedding model changed: run an incremental/full re-index. |
| Status `failed`/`cancelled` | Fix the reported error and re-run indexing; use *Full re-index* if it persists. |
| "index is stale" | Files changed after indexing → *Update index*. Set `CODEMIND_REQUIRE_FRESH_INDEX=true` to refuse stale answers. |
| ChromaDB errors / corrupt index | `CODEMIND_VECTOR_BACKEND=sqlite` or delete the index and re-index. |
| Port 8000 busy | `python main.py serve --port 8010`. |
| "Insufficient disk space" | Free space or lower `CODEMIND_MIN_FREE_DISK_MB`. |
| Large repositories slow | Add `CODEMIND_EXCLUDE_PATTERNS=*.generated.*,docs/archive/*`; raise nothing blindly. |
| Windows script blocked | `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1`. |
