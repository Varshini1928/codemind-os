from types import SimpleNamespace as NS

from backend.authz import MISSING, authorize, csrf_ok

ADMIN = NS(id="a", role="admin", must_change_password=False)
USER = NS(id="u", role="user", must_change_password=False)
OTHER = NS(id="o", role="user", must_change_password=False)
OWNERS = {"r1": "u", "r2": "o", "legacy": None}
OWN = lambda rid: OWNERS.get(rid, MISSING)  # noqa: E731
JOB = lambda jid: {"j1": "r1", "j2": "r2"}.get(jid)  # noqa: E731


def az(method, path, user):
    return authorize(method, path, user, OWN, JOB)


def test_public_and_non_api_paths():
    for p in ["/", "/static/js/main.js", "/api/health", "/api/session", "/api/auth/login", "/api/auth/signup"]:
        assert az("GET", p, None)[0]
    assert az("GET", "/api/repositories", None)[:2] == (False, 401)


def test_admin_only_routes():
    assert az("GET", "/api/admin/users", USER)[:2] == (False, 403)
    assert az("GET", "/api/admin/users", ADMIN)[0]
    assert az("PUT", "/api/settings", USER)[:2] == (False, 403) and az("GET", "/api/settings", USER)[0] and az("PUT", "/api/settings", ADMIN)[0]


def test_repository_isolation():
    assert az("GET", "/api/repositories/r1/overview", USER)[0]
    assert az("GET", "/api/repositories/r2/overview", USER)[:2] == (False, 404)
    assert az("DELETE", "/api/repositories/r2", USER)[:2] == (False, 404)
    assert az("GET", "/api/repositories/legacy", USER)[:2] == (False, 404)  # unowned = admin only
    assert az("GET", "/api/repositories/r2/overview", ADMIN)[0]
    assert az("GET", "/api/repositories/missing/overview", USER)[0]  # handler answers 404 itself
    assert az("GET", "/api/repositories/r1/source", OTHER)[:2] == (False, 404)  # no existence leak


def test_jobs_and_reports_follow_repository_ownership():
    assert az("GET", "/api/jobs/j1", USER)[0] and az("GET", "/api/jobs/j2", USER)[:2] == (False, 404)
    assert az("POST", "/api/jobs/j2/cancel", USER)[:2] == (False, 404) and az("GET", "/api/jobs/j2", ADMIN)[0]
    assert az("GET", "/api/jobs/unknown", USER)[:2] == (False, 404)
    assert az("GET", "/api/reports/r1/x", USER)[0] and az("GET", "/api/reports/r2/x", USER)[:2] == (False, 404)


def test_must_change_password_lockdown():
    u = NS(id="u", role="user", must_change_password=True)
    assert az("GET", "/api/repositories", u)[:2] == (False, 403)
    assert az("POST", "/api/me/password", u)[0] and az("GET", "/api/me", u)[0] and az("POST", "/api/auth/logout", u)[0]


def test_csrf_header_rules():
    assert csrf_ok("GET", {}) and csrf_ok("HEAD", {})
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not csrf_ok(m, {}) and not csrf_ok(m, {"x-requested-with": "other"}) and csrf_ok(m, {"x-requested-with": "codemind"})
