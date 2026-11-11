# API reference (FastAPI, `http://127.0.0.1:8000/api`, interactive docs at `/api/docs` in local mode)

All routes live under **`/api`** (the web UI is served at `/`). Authentication is a session cookie set by `POST /api/auth/login`; every `POST/PUT/PATCH/DELETE` must also send the header `X-Requested-With: codemind`. Public routes: `GET /api/health`, `GET /api/session`, `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`. Everything else needs a signed-in, active user; `/api/admin/*` and `PUT /api/settings` need the admin role; repository, job and report routes only work for the repository's owner (or an admin) and otherwise answer 404.

Errors: `{"error": "<kind>", "detail": "<safe message>"}` — 400 rejected by security policy · 403 approval required/disabled · 404 not found · 409 index not ready · 413 body too large · 422 invalid request · 500 internal (details only in the local log).

| Method & path | Purpose |
|---|---|
| `GET /api/health` · `GET /api/ready` | liveness (public) · DB integrity, vector store, git, Ollama/model status |
| `GET /api/session` | public: hosted flag, auth mode, sign-up policy, whether this browser is signed in (and as whom) |
| `POST /api/auth/signup {email,password,display_name}` | create an account (`pending` or `active` depending on the sign-up policy) |
| `POST /api/auth/login {email,password}` · `POST /api/auth/logout` | start / end a session (cookie) |
| `GET /api/me` · `PATCH /api/me {display_name}` | profile, usage statistics, recent activity |
| `POST /api/me/password {current_password,new_password}` · `POST /api/me/sessions/revoke-others` | change password · sign out other devices |
| `GET /api/admin/users` · `PATCH /api/admin/users/{id} {role?,status?}` | list users · approve/disable/enable, change role |
| `POST /api/admin/users/{id}/reset-password` · `DELETE /api/admin/users/{id}` | temporary password (returned once) · delete user and their data |
| `GET /api/admin/stats` · `/api/admin/audit?limit=` · `/api/admin/repositories` | server statistics · audit log · all repositories with owners |
| `GET /repositories/{id}/source?path=&start=&end=` | indexed source lines (used by the UI's evidence viewer) |
| `GET /settings` · `PUT /settings {key,value}` | settings (secrets none) with storage locations · change a whitelisted runtime setting |
| `POST /repositories {path}` or `{git_url}` | register (and clone, if a Git URL). Local paths are rejected in hosted mode. |
| `GET /repositories?scope=mine|all` (`all` = admin only) · `GET /repositories/{id}` | list · status incl. `stale`, warnings |
| `POST /repositories/{id}/index {full}` → 202 `{job_id}` | start incremental or full indexing |
| `GET /jobs/{job}` · `POST /jobs/{job}/cancel` | progress/logs/result · cancel |
| `DELETE /repositories/{id}/index` · `DELETE /repositories/{id}` | delete index · unregister |
| `GET /repositories/{id}/overview` | languages, entities, entry points, endpoints, config keys, exclusions |
| `GET /repositories/{id}/files?language=&q=` | indexed files |
| `GET /repositories/{id}/search?q=&kind=evidence|symbol&mode=&k=` | evidence bundle (with text) or symbol matches |
| `GET /repositories/{id}/graph/neighborhood?node=&depth=&direction=&intent=` · `/graph/export` | subgraph · full JSON export |
| `POST /repositories/{id}/ask {question,use_llm,save_report}` | question answering / multi-part requests |
| `POST /repositories/{id}/quality-review {focus,paths}` | candidate findings |
| `POST /repositories/{id}/impact {target,change,depth}` | change-impact report |
| `POST /repositories/{id}/test-plan {target}` · `/test-plan/export` | test specifications · write to the review dir |
| `POST /repositories/{id}/documentation {kind}` | Markdown docs |
| `POST /repositories/{id}/files/write {rel_path,content,approved}` | gated write into the repo |
| `POST /repositories/{id}/tests/run {approved}` | gated, opt-in test run |
| `GET /reports?repo_id=` · `GET /reports/{repo}/{id}?fmt=markdown|json` · `DELETE …` | saved reports |

Paths below are relative to `/api` (e.g. `POST /api/repositories`).

Example:
```bash
curl -s localhost:8000/api/repositories -H 'content-type: application/json' -d '{"path": "/home/me/proj"}'
curl -s -X POST localhost:8000/api/repositories/<id>/index -H 'content-type: application/json' -d '{}'
curl -s localhost:8000/api/repositories/<id>/ask -H 'content-type: application/json' \
     -d '{"question": "Where is apply_discount defined?", "use_llm": false}'
```
Result shape (abridged): `{status: complete|partial|insufficient_evidence, summary, sections:[{task, summary, mode, claims:[{text, kind, evidence_ids, status, notes}], payload}], evidence:[{id, path, start_line, end_line, revision, symbol, methods, scores, uncertainty, text}], verification:{total, citation_valid, unverified, removed, citation_coverage, note}, uncertainty, errors, ai:{used, model, error}, engine, revision, stale_index}`.
