"""FastAPI service. Binds to localhost by default (see main.py). All data stays on this machine."""
from __future__ import annotations

import logging

import shutil
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend import schemas as S
from backend import views
from backend.auth import RateLimiter
from backend.authz import MISSING, authorize, csrf_ok
from backend.dependencies import Services, get_services, set_override
from graph.dependency_analyzer import INTENT_EDGES, resolve_target, subgraph_view
from graph.graph_store import GraphCorruptError
from retrieval.hybrid_search import MODES, IndexNotReadyError
from retrieval.vector_store import VectorStoreError
from services.auth_service import LOCAL_ADMIN, AuthError
from services.indexing_service import NotFoundError
from services.test_execution import run_tests
from verification.safety_policy import ApprovalRequired, SecurityError

log = logging.getLogger("codemind.api")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
VERSION = "1.2.0"
COOKIE = "codemind_session"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def create_app(services: Services | None = None) -> FastAPI:
    svc = services or get_services()
    st = svc.settings
    if st.hosted and svc.auth.admin_count() == 0:
        raise RuntimeError("Hosted mode needs an administrator account: set CODEMIND_ADMIN_EMAIL and CODEMIND_ADMIN_PASSWORD.")
    login_limit, signup_limit = RateLimiter(20, 60), RateLimiter(5, 3600)
    app = FastAPI(title="CodeMind OS API", version=VERSION, docs_url=None if svc.settings.hosted else "/api/docs",
                  openapi_url=None if svc.settings.hosted else "/api/openapi.json",
                  redoc_url=None, description="Evidence-first repository intelligence.")
    api = APIRouter(prefix="/api")

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return (fwd.split(",")[0].strip() if svc.settings.hosted and fwd else (request.client.host if request.client else "?"))

    def repo_owner(repo_id: str):
        row = svc.db.one("SELECT owner_id FROM repositories WHERE id=?", (repo_id,))
        return MISSING if row is None else row["owner_id"]

    def job_repo(job_id: str):
        row = svc.db.one("SELECT repo_id FROM jobs WHERE id=?", (job_id,))
        return row["repo_id"] if row else None

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        token = request.cookies.get(COOKIE)
        user = LOCAL_ADMIN if st.auth_mode == "none" else svc.auth.user_for_token(token)
        request.state.user, request.state.token = user, token
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > st.max_request_bytes:
            resp = JSONResponse({"error": "request_too_large", "detail": "Request body exceeds the configured limit."}, 413)
        elif path.startswith("/api"):
            if st.auth_mode == "accounts" and not csrf_ok(request.method, request.headers):
                resp = JSONResponse({"error": "csrf", "detail": "Missing X-Requested-With header."}, 403)
            else:
                ok, status, msg = authorize(request.method, path, user, repo_owner, job_repo)
                resp = await call_next(request) if ok else JSONResponse(
                    {"error": {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(status, "denied"), "detail": msg}, status)
        else:
            resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if not path.startswith("/api/docs"):  # Swagger UI needs inline scripts/CDN assets; everything else is locked down
            resp.headers["Content-Security-Policy"] = CSP
        if path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return fwd.split(",")[0].strip() if st.hosted and fwd else (request.client.host if request.client else "?")

    def set_cookie(request: Request, response: Response, token: str) -> None:
        secure = {"true": True, "false": False}.get(st.cookie_secure, request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https")
        response.set_cookie(COOKIE, token, max_age=st.session_ttl_hours * 3600, httponly=True, samesite="strict", secure=secure, path="/")

    def err(status: int, kind: str, detail: str):
        return JSONResponse({"error": kind, "detail": detail}, status_code=status)

    @app.exception_handler(AuthError)
    async def _auth(_, e): return err(e.status, e.kind, e.detail)

    @app.exception_handler(NotFoundError)
    async def _nf(_, e): return err(404, "not_found", str(e))

    @app.exception_handler(IndexNotReadyError)
    async def _nr(_, e): return err(409, "index_not_ready", str(e))

    @app.exception_handler(ApprovalRequired)
    async def _ap(_, e): return err(403, "approval_required", str(e))

    @app.exception_handler(SecurityError)
    async def _se(_, e): return err(400, "rejected_by_security_policy", str(e))

    @app.exception_handler(ValueError)
    async def _ve(_, e): return err(422, "invalid_request", str(e))

    @app.exception_handler(GraphCorruptError)
    async def _gc(_, e): return err(500, "index_corrupt", "The stored graph is damaged; run a full re-index.")

    @app.exception_handler(VectorStoreError)
    async def _vs(_, e): return err(500, "vector_store_error", "The vector store failed; run a full re-index.")

    @app.exception_handler(Exception)
    async def _any(_, e):
        log.exception("Unhandled error")
        return err(500, "internal_error", "Unexpected error; details were written to the local log.")

    # ------------------------------------------------------------ health / session
    @api.get("/health")
    def health():
        return {"status": "ok", "version": VERSION}

    @api.get("/session")
    def session(request: Request):
        u = request.state.user
        return {"app": "CodeMind OS", "version": VERSION, "hosted": st.hosted, "auth_mode": st.auth_mode, "authenticated": u is not None,
                "user": u.public() if u else None, "signup_mode": st.signup_mode,
                "bootstrap": st.auth_mode == "accounts" and not st.hosted and svc.auth.count_users() == 0,
                "clone_enabled": st.clone_workspace is not None, "local_paths_enabled": bool(u and u.is_admin and not st.hosted),
                "allowed_git_hosts": st.allowed_git_hosts, "max_repos_per_user": st.max_repos_per_user}

    # ------------------------------------------------------------ accounts
    @api.post("/auth/signup", status_code=201)
    def signup(body: S.SignupRequest, request: Request):
        if st.auth_mode != "accounts":
            raise AuthError(404, "not_found", "Accounts are disabled on this server.")
        if not signup_limit.allow(client_ip(request)):
            raise AuthError(429, "rate_limited", "Too many sign-ups from this address. Try again later.")
        user = svc.auth.signup(body.email, body.password, body.display_name)
        msg = ("Account created. You can sign in now." if user.status == "active"
               else "Account created. An administrator must approve it before you can sign in.")
        return {"user": user.public(), "pending": user.status == "pending", "message": msg}

    @api.post("/auth/login")
    def login(body: S.LoginRequest, request: Request, response: Response):
        if not login_limit.allow(client_ip(request)):
            raise AuthError(429, "rate_limited", "Too many attempts. Wait a minute and try again.")
        token, user = svc.auth.login(body.email, body.password, client_ip(request), request.headers.get("user-agent", ""))
        set_cookie(request, response, token)
        return {"user": user.public(), "must_change_password": user.must_change_password}

    @api.post("/auth/logout")
    def logout(request: Request, response: Response):
        svc.auth.revoke(request.state.token)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/me")
    def me(request: Request):
        return views.me_payload(svc, request.state.user)

    @api.patch("/me")
    def update_me(body: S.ProfileUpdate, request: Request):
        return {"user": svc.auth.update_profile(request.state.user, body.display_name).public()}

    @api.post("/me/password")
    def change_password(body: S.PasswordChange, request: Request):
        svc.auth.change_password(request.state.user, body.current_password, body.new_password, request.state.token)
        return {"ok": True}

    @api.post("/me/sessions/revoke-others")
    def revoke_others(request: Request):
        return {"revoked": svc.auth.revoke_user_sessions(request.state.user.id, request.state.token)}

    # ------------------------------------------------------------ administration (role checked in the gate)
    @api.get("/admin/users")
    def admin_users():
        return views.admin_users(svc)

    @api.patch("/admin/users/{user_id}")
    def admin_update_user(user_id: str, body: S.AdminUserUpdate, request: Request):
        return svc.auth.admin_update(request.state.user, user_id, body.role, body.status).public()

    @api.post("/admin/users/{user_id}/reset-password")
    def admin_reset_password(user_id: str, request: Request):
        return {"temporary_password": svc.auth.reset_password(request.state.user, user_id),
                "note": "Shown once. The user must choose a new password at next sign-in."}

    @api.delete("/admin/users/{user_id}", status_code=204)
    def admin_delete_user(user_id: str, request: Request):
        svc.auth.delete_user(request.state.user, user_id)

    @api.get("/admin/stats")
    def admin_stats():
        return views.admin_stats(svc)

    @api.get("/admin/audit")
    def admin_audit(limit: int = Query(100, ge=1, le=500)):
        return svc.auth.audit_list(limit)

    @api.get("/admin/repositories")
    def admin_repositories():
        return views.admin_repos(svc)

    @api.get("/ready")
    def ready():
        llm = svc.analysis.llm_status()
        db_ok = svc.db.integrity_ok()
        return {"ready": db_ok, "database_ok": db_ok, "vector_store": getattr(svc.vectors, "name", "?"),
                "git_available": bool(shutil.which("git")), "hosted": svc.settings.hosted,
                "llm": {"available": llm["available"], "model": llm["model"], "model_installed": llm["model_installed"],
                        "message": llm["error"]},
                "note": "Without a local model, retrieval/graph/rule-based analysis still works (AI interpretation is skipped)."}

    @api.get("/settings")
    def settings(request: Request):
        d = svc.settings.public_dict()
        if request.state.user.is_admin:
            return d
        keep = {"ollama_model", "allow_repo_writes", "allow_test_execution", "allow_remote_ollama", "hosted", "signup_mode",
                "max_repos_per_user", "top_k", "alpha", "beta", "gamma", "delta", "max_file_bytes", "max_files"}
        return {k: v for k, v in d.items() if k in keep}

    @api.put("/settings")
    def update_setting(body: S.SettingUpdate):
        set_override(svc, body.key, body.value)
        return {"updated": body.key}

    # ------------------------------------------------------------ repositories
    @api.post("/repositories", status_code=201)
    def register(body: S.RegisterRequest, request: Request):
        u = request.state.user
        if body.path and (st.hosted or not u.is_admin):
            raise SecurityError("Local folders can only be added by an administrator on a local install; add a public Git URL instead.")
        if st.max_repos_per_user and not u.is_admin and svc.indexing.count_owned(u.id) >= st.max_repos_per_user:
            raise SecurityError(f"You have reached the limit of {st.max_repos_per_user} repositories. Remove one first.")
        owner = None if u.id == "local" else u.id
        repo = svc.indexing.register(body.path, body.git_url, body.dest_name if u.is_admin else None, owner)
        if owner:
            svc.auth.audit(u, "repo_registered", repo["name"])
        return repo

    @api.get("/repositories")
    def list_repos(request: Request, scope: str = Query("mine", pattern="^(mine|all)$")):
        u = request.state.user
        if u.id == "local" or (u.is_admin and scope == "all"):
            return svc.indexing.list_repos()
        return svc.indexing.list_repos(u.id)

    @api.get("/repositories/{repo_id}")
    def repo_status(repo_id: str):
        return svc.indexing.status(repo_id)

    @api.delete("/repositories/{repo_id}/index", status_code=204)
    def delete_index(repo_id: str):
        svc.indexing.delete_index(repo_id)

    @api.delete("/repositories/{repo_id}", status_code=204)
    def unregister(repo_id: str, request: Request):
        repo = svc.indexing.require(repo_id)
        svc.indexing.unregister(repo_id)
        if request.state.user.id != "local":
            svc.auth.audit(request.state.user, "repo_removed", repo["name"])

    @api.post("/repositories/{repo_id}/index", status_code=202)
    def start_index(repo_id: str, body: S.IndexRequest = S.IndexRequest()):
        return {"job_id": svc.indexing.start_index(repo_id, body.full)}

    @api.get("/jobs/{job_id}")
    def job(job_id: str):
        j = svc.jobs.get(job_id)
        if j is None:
            raise NotFoundError("Job not found.")
        return j

    @api.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return {"cancel_requested": svc.jobs.cancel(job_id)}

    @api.get("/repositories/{repo_id}/overview")
    def overview(repo_id: str):
        return svc.indexing.overview(repo_id)

    @api.get("/repositories/{repo_id}/files")
    def files(repo_id: str, language: str | None = None, q: str | None = Query(None, max_length=200)):
        return svc.indexing.list_files(repo_id, language, q)

    @api.get("/repositories/{repo_id}/search")
    def search(repo_id: str, q: str = Query(..., min_length=1, max_length=500),
               kind: str = Query("evidence", pattern="^(evidence|symbol)$"), mode: str = "hybrid", k: int = Query(10, ge=1, le=50)):
        svc.indexing.require(repo_id)
        if kind == "symbol":
            return svc.indexing.find_symbols(repo_id, q, k)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        return svc.searcher.search(repo_id, q, "question", mode, k, stale=svc.indexing.is_stale(repo_id)).to_dict(include_text=True)

    @api.get("/repositories/{repo_id}/graph/neighborhood")
    def neighborhood(repo_id: str, node: str = Query(..., max_length=512), depth: int = Query(1, ge=1, le=4),
                     direction: str = Query("both", pattern="^(in|out|both)$"), intent: str | None = None):
        svc.indexing.require(repo_id)
        g = svc.graphs.load(repo_id)
        res = resolve_target(g, node)
        if not res["ids"]:
            raise NotFoundError(f"Node '{node}' not found" + (" (ambiguous)" if res["candidates"] else "") + ".")
        et = INTENT_EDGES.get(intent) if intent else None
        return subgraph_view(g, res["ids"][0], depth, direction, et)

    @api.get("/repositories/{repo_id}/graph/export")
    def graph_export(repo_id: str):
        svc.indexing.require(repo_id)
        return svc.graphs.export_json(repo_id)

    @api.get("/repositories/{repo_id}/source")
    def source(repo_id: str, path: str = Query(..., max_length=512), start: int = Query(1, ge=1), end: int = Query(400, ge=1)):
        repo = svc.indexing.require(repo_id)
        lines = svc.chunks.file_lines(repo_id, path)
        if not lines:
            raise NotFoundError("File is not part of the index.")
        end = min(end, start + 1999, len(lines))
        return {"path": path, "revision": repo["revision"], "start_line": start, "end_line": end,
                "total_lines": len(lines), "lines": lines[start - 1:end],
                "note": "Lines come from the indexed revision (blank gaps between chunks are shown empty)."}

    # ------------------------------------------------------------ analyses
    @api.post("/repositories/{repo_id}/ask")
    def ask(repo_id: str, body: S.QuestionRequest):
        return svc.analysis.ask(repo_id, body.question, use_llm=body.use_llm, save=body.save_report)

    @api.post("/repositories/{repo_id}/quality-review")
    def quality(repo_id: str, body: S.QualityRequest):
        return svc.analysis.quality_review(repo_id, body.focus, body.paths, use_llm=body.use_llm, save=body.save_report)

    @api.post("/repositories/{repo_id}/impact")
    def impact(repo_id: str, body: S.ImpactRequest):
        return svc.analysis.impact(repo_id, body.target, body.change, body.depth, use_llm=body.use_llm, save=body.save_report)

    @api.post("/repositories/{repo_id}/test-plan")
    def test_plan(repo_id: str, body: S.TestPlanRequest):
        return svc.analysis.test_plan(repo_id, body.target, use_llm=body.use_llm, save=body.save_report)

    @api.post("/repositories/{repo_id}/documentation")
    def documentation(repo_id: str, body: S.DocumentationRequest):
        return svc.analysis.document(repo_id, body.kind, use_llm=body.use_llm, save=body.save_report)

    @api.post("/repositories/{repo_id}/test-plan/export")
    def export_tests(repo_id: str, body: S.ExportTestsRequest):
        from agents.test_planner import to_markdown
        res = svc.analysis.test_plan(repo_id, body.target, use_llm=False, save=False)
        plan = next((s["payload"] for s in res["sections"] if "tests" in s["payload"]), {"tests": []})
        return svc.reports.export_test_proposals(repo_id, to_markdown(plan), plan)

    @api.post("/repositories/{repo_id}/files/write")
    def write_file(repo_id: str, body: S.WriteFileRequest):
        from pathlib import Path
        repo = svc.indexing.require(repo_id)
        return {"written": svc.reports.write_into_repository(Path(repo["root"]), body.rel_path, body.content, body.approved)}

    @api.post("/repositories/{repo_id}/tests/run")
    def run_repo_tests(repo_id: str, body: S.RunTestsRequest):
        from pathlib import Path
        from ingestion.manifest import Manifest
        repo = svc.indexing.require(repo_id)
        row = svc.db.one("SELECT json FROM manifests WHERE repo_id=?", (repo_id,))
        if not row:
            raise IndexNotReadyError("Index the repository first.")
        return run_tests(svc.settings, Path(repo["root"]), Manifest.from_json(row["json"]), body.approved)

    # ------------------------------------------------------------ reports
    @api.get("/reports")
    def reports(request: Request, repo_id: str | None = None):
        u = request.state.user
        if repo_id:
            row = svc.db.one("SELECT owner_id FROM repositories WHERE id=?", (repo_id,))
            if row is None or (not u.is_admin and row["owner_id"] != u.id):
                raise NotFoundError("Repository not found.")
            return svc.reports.list(repo_id)
        owned = {r["id"] for r in (svc.indexing.list_repos() if u.id == "local" else svc.indexing.list_repos(u.id))}
        return [r for r in svc.reports.list() if r["repository_id"] in owned]

    @api.get("/reports/{repo_id}/{report_id}")
    def report(repo_id: str, report_id: str, fmt: str = Query("markdown", pattern="^(markdown|json)$")):
        try:
            text = svc.reports.read(repo_id, report_id, fmt)
        except FileNotFoundError:
            raise NotFoundError("Report not found.") from None
        return PlainTextResponse(text, media_type="text/markdown" if fmt == "markdown" else "application/json")

    @api.delete("/reports/{repo_id}/{report_id}", status_code=204)
    def delete_report(repo_id: str, report_id: str):
        if not svc.reports.delete(repo_id, report_id):
            raise NotFoundError("Report not found.")

    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def index():
        page = WEB_DIR / "index.html"
        if not page.is_file():
            return PlainTextResponse("Web UI files are missing (web/index.html). The API is available under /api.", 404)
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
