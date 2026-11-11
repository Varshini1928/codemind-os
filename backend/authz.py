"""Pure access-control rules (no web framework): who may call which API path."""
from __future__ import annotations

import re
from typing import Callable

PUBLIC_PATHS = {"/api/health", "/api/session", "/api/auth/login", "/api/auth/signup", "/api/auth/logout"}
MUST_CHANGE_ALLOWED = PUBLIC_PATHS | {"/api/me", "/api/me/password"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "codemind"
_REPO = re.compile(r"^/api/repositories/([^/]+)")
_JOB = re.compile(r"^/api/jobs/([^/]+)")
_REPORT = re.compile(r"^/api/reports/([^/]+)/")
MISSING = object()


def csrf_ok(method: str, headers) -> bool:
    """State-changing requests must carry a custom header, which cross-site forms cannot set."""
    return method.upper() in SAFE_METHODS or headers.get(CSRF_HEADER, "") == CSRF_VALUE


def authorize(method: str, path: str, user, repo_owner: Callable[[str], object], job_repo: Callable[[str], str | None]) -> tuple[bool, int, str]:
    """user: object with .id .role .must_change_password, or None. repo_owner(repo_id) -> owner id | None | MISSING."""
    if not path.startswith("/api") or path in PUBLIC_PATHS:
        return True, 200, ""
    if user is None:
        return False, 401, "Please sign in."
    if user.must_change_password and path not in MUST_CHANGE_ALLOWED:
        return False, 403, "You must change your temporary password first."
    admin = user.role == "admin"
    if path.startswith("/api/admin"):
        return (True, 200, "") if admin else (False, 403, "Administrator access required.")
    if method.upper() != "GET" and path == "/api/settings" and not admin:
        return False, 403, "Only administrators can change settings."
    repo_id = None
    m = _REPO.match(path) or _REPORT.match(path)
    if m:
        repo_id = m.group(1)
    else:
        j = _JOB.match(path)
        if j:
            repo_id = job_repo(j.group(1))
            if repo_id is None:
                return (True, 200, "") if admin else (False, 404, "Not found.")
    if repo_id is not None and not admin:
        owner = repo_owner(repo_id)
        if owner is not MISSING and owner != user.id:
            return False, 404, "Repository not found."  # same answer as for a missing repository: no existence leak
        if owner is MISSING:
            return True, 200, ""  # handler will answer 404 itself
    return True, 200, ""
