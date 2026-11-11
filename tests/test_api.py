import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402

PW = "correct horse 42"
PW2 = "another phrase 77"
H = {"X-Requested-With": "codemind"}


def make_client(app):
    return TestClient(app, headers=H)


def sign_in(client, email, pw=PW, name=""):
    r = client.post("/api/auth/signup", json={"email": email, "password": pw, "display_name": name})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture
def app(services):
    return create_app(services)


@pytest.fixture
def client(app):
    c = make_client(app)
    sign_in(c, "admin@example.com", name="Admin")  # first account on a local install becomes the administrator
    return c


def test_public_endpoints_and_session(app):
    c = make_client(app)
    assert c.get("/api/health").json()["status"] == "ok"
    s = c.get("/api/session").json()
    assert s["authenticated"] is False and s["bootstrap"] is True and s["signup_mode"] == "open" and s["user"] is None
    assert c.get("/api/repositories").status_code == 401
    assert c.get("/api/ready").status_code == 401


def test_csrf_header_required(app):
    c = TestClient(app)  # no X-Requested-With
    r = c.post("/api/auth/signup", json={"email": "a@example.com", "password": PW})
    assert r.status_code == 403 and r.json()["error"] == "csrf"
    assert c.get("/api/health").status_code == 200  # safe methods are fine


def test_signup_login_logout_cookie(app):
    c = make_client(app)
    assert c.post("/api/auth/signup", json={"email": "bad", "password": PW}).status_code == 422
    assert c.post("/api/auth/signup", json={"email": "a@example.com", "password": "short"}).status_code == 422
    user = sign_in(c, "a@example.com", name="Ann")
    assert user["role"] == "admin" and user["display_name"] == "Ann"
    r = c.post("/api/auth/login", json={"email": "a@example.com", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "codemind_session=" in cookie
    assert "password_hash" not in r.text
    assert c.get("/api/session").json()["user"]["email"] == "a@example.com"
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong password 9"}).status_code == 401
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401


def test_me_profile_and_password(client):
    me = client.get("/api/me").json()
    assert me["user"]["email"] == "admin@example.com" and me["stats"]["repositories"] == 0
    assert client.patch("/api/me", json={"display_name": "New Name"}).json()["user"]["display_name"] == "New Name"
    assert client.post("/api/me/password", json={"current_password": "nope nope 12", "new_password": PW2}).status_code == 403
    assert client.post("/api/me/password", json={"current_password": PW, "new_password": "short"}).status_code == 422
    assert client.post("/api/me/password", json={"current_password": PW, "new_password": PW2}).status_code == 200
    assert client.get("/api/me").status_code == 200  # current session survives
    assert client.post("/api/me/sessions/revoke-others").json()["revoked"] == 0


def test_approval_flow_and_admin_page(app, services, client):
    services.settings.signup_mode = "approval"
    u = make_client(app)
    r = u.post("/api/auth/signup", json={"email": "u@example.com", "password": PW})
    assert r.status_code == 201 and r.json()["pending"] is True
    assert u.post("/api/auth/login", json={"email": "u@example.com", "password": PW}).status_code == 403
    users = client.get("/api/admin/users").json()
    uid = next(x["id"] for x in users if x["email"] == "u@example.com")
    assert client.patch(f"/api/admin/users/{uid}", json={"status": "active"}).json()["status"] == "active"
    assert u.post("/api/auth/login", json={"email": "u@example.com", "password": PW}).status_code == 200
    assert u.get("/api/admin/users").status_code == 403 and u.get("/api/admin/stats").status_code == 403
    assert client.get("/api/admin/stats").json()["users"]["total"] == 2
    assert any(a["action"] == "user_approved" for a in client.get("/api/admin/audit").json())
    assert client.patch(f"/api/admin/users/{uid}", json={"role": "boss"}).status_code == 422
    t = client.post(f"/api/admin/users/{uid}/reset-password").json()["temporary_password"]
    assert u.get("/api/me").status_code == 401  # sessions revoked
    u.post("/api/auth/login", json={"email": "u@example.com", "password": t})
    assert u.get("/api/repositories").status_code == 403  # must change password first
    assert u.post("/api/me/password", json={"current_password": t, "new_password": PW2}).status_code == 200
    assert u.get("/api/repositories").status_code == 200
    assert client.delete(f"/api/admin/users/{uid}").status_code == 204
    me_id = client.get("/api/me").json()["user"]["id"]
    assert client.delete(f"/api/admin/users/{me_id}").status_code == 409


def test_users_cannot_see_each_others_repositories(app, services, client, sample_repo):
    rid = client.post("/api/repositories", json={"path": str(sample_repo)}).json()["id"]
    services.indexing.index_now(rid)
    b = make_client(app)
    sign_in(b, "b@example.com")
    assert b.get("/api/repositories").json() == []
    assert b.get(f"/api/repositories/{rid}").status_code == 404
    assert b.get(f"/api/repositories/{rid}/overview").status_code == 404
    assert b.post(f"/api/repositories/{rid}/ask", json={"question": "where is add_stock", "use_llm": False}).status_code == 404
    assert b.get(f"/api/repositories/{rid}/source", params={"path": "inventory/service.py"}).status_code == 404
    assert b.get("/api/reports", params={"repo_id": rid}).status_code == 404
    assert b.delete(f"/api/repositories/{rid}").status_code == 404
    assert b.post("/api/repositories", json={"path": str(sample_repo)}).status_code == 400  # local folders: admins only
    assert b.put("/api/settings", json={"key": "top_k", "value": 3}).status_code == 403
    assert "storage_locations" not in b.get("/api/settings").json()
    assert client.get("/api/settings").json()["storage_locations"]
    assert client.get(f"/api/repositories/{rid}").status_code == 200
    assert [r["id"] for r in client.get("/api/admin/repositories").json()] == [rid]


def test_full_flow_as_admin(client, sample_repo, services):
    assert client.post("/api/repositories", json={}).status_code == 400
    assert client.post("/api/repositories", json={"path": "/definitely/missing"}).status_code == 400
    assert client.post("/api/repositories", json={"path": "x" * 5000}).status_code == 422
    rid = client.post("/api/repositories", json={"path": str(sample_repo)}).json()["id"]
    assert client.post(f"/api/repositories/{rid}/ask", json={"question": "where is add_stock", "use_llm": False}).status_code == 409
    job = client.post(f"/api/repositories/{rid}/index", json={}).json()["job_id"]
    assert services.jobs.wait(job, 120)["status"] == "succeeded"
    assert client.get(f"/api/jobs/{job}").json()["status"] == "succeeded"
    ov = client.get(f"/api/repositories/{rid}/overview").json()
    assert ov["languages"]["python"] >= 5 and len(ov["endpoints"]) == 3
    hits = client.get(f"/api/repositories/{rid}/search", params={"q": "apply_discount"}).json()
    assert hits["items"][0]["path"] == "inventory/service.py"
    assert client.get(f"/api/repositories/{rid}/search", params={"q": "x", "mode": "bogus"}).status_code == 422
    nb = client.get(f"/api/repositories/{rid}/graph/neighborhood", params={"node": "add_stock", "depth": 2}).json()
    assert nb["nodes"] and nb["edges"]
    assert client.get(f"/api/repositories/{rid}/graph/neighborhood", params={"node": "zzz"}).status_code == 404
    ask = client.post(f"/api/repositories/{rid}/ask", json={"question": "How is stock added?", "use_llm": False}).json()
    assert ask["status"] == "complete" and ask["report"]["report_id"]
    assert client.post(f"/api/repositories/{rid}/quality-review", json={"use_llm": False}).json()["sections"][0]["payload"]["findings"]
    assert client.post(f"/api/repositories/{rid}/impact", json={"target": "add_stock", "use_llm": False}).status_code == 200
    assert client.post(f"/api/repositories/{rid}/test-plan", json={"target": "apply_discount", "use_llm": False}).status_code == 200
    assert client.post(f"/api/repositories/{rid}/documentation", json={"kind": "bogus"}).status_code == 422
    src = client.get(f"/api/repositories/{rid}/source", params={"path": "inventory/service.py", "start": 1, "end": 5}).json()
    assert src["lines"][0].startswith('"""Stock')
    assert client.get(f"/api/repositories/{rid}/source", params={"path": "../../etc/passwd"}).status_code == 404
    reports = client.get("/api/reports", params={"repo_id": rid}).json()
    assert reports and client.get(f"/api/reports/{rid}/{reports[0]['report_id']}").status_code == 200
    assert client.post(f"/api/repositories/{rid}/files/write", json={"rel_path": "a.md", "content": "x", "approved": True}).status_code == 403
    assert client.post(f"/api/repositories/{rid}/tests/run", json={"approved": True}).status_code == 403
    assert client.get("/api/me").json()["stats"]["repositories"] == 1
    assert client.delete(f"/api/repositories/{rid}/index").status_code == 204


def test_ui_headers_and_settings(app, client):
    r = client.get("/")
    assert r.status_code == 200 and "CodeMind OS" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert client.get("/static/js/main.js").status_code == 200
    s = client.get("/api/settings").json()
    assert s["allow_repo_writes"] is False and s["admin_password"] == "(not set)"
    assert client.put("/api/settings", json={"key": "allow_repo_writes", "value": True}).status_code == 422
    assert client.put("/api/settings", json={"key": "top_k", "value": 7}).status_code == 200
    big = client.post("/api/auth/login", content=b"x" * 2_000_000, headers={"content-type": "application/json", **H})
    assert big.status_code == 413


def test_signup_closed_and_rate_limit(app, services):
    services.settings.signup_mode = "closed"
    c = make_client(app)
    assert c.post("/api/auth/signup", json={"email": "first@example.com", "password": PW}).status_code == 201  # bootstrap admin
    assert c.post("/api/auth/signup", json={"email": "second@example.com", "password": PW}).status_code == 403


def test_hosted_mode(tmp_path):
    from backend.dependencies import build_services
    from config.settings import load_settings
    env = {"CODEMIND_HOSTED": "true", "CODEMIND_ALLOW_TEST_EXECUTION": "true", "CODEMIND_ALLOW_REPO_WRITES": "true"}
    kw = dict(storage_dir=tmp_path / "h", embedding_backend="hashing", vector_backend="sqlite", password_iterations=1000)
    s = load_settings(env=env, **kw)
    assert s.allow_test_execution is False and s.allow_repo_writes is False and s.signup_mode == "approval" and s.auth_mode == "accounts"
    svc = build_services(s)
    with pytest.raises(RuntimeError):
        create_app(svc)  # refuses to start without an administrator
    svc.db.close()
    s2 = load_settings(env={**env, "CODEMIND_ADMIN_EMAIL": "root@example.com", "CODEMIND_ADMIN_PASSWORD": PW}, **{**kw, "storage_dir": tmp_path / "h2"})
    svc2 = build_services(s2)
    app = create_app(svc2)
    c = make_client(app)
    assert c.get("/api/session").json()["bootstrap"] is False  # no first-visitor admin takeover on a public server
    r = c.post("/api/auth/signup", json={"email": "visitor@example.com", "password": PW})
    assert r.json()["pending"] is True and r.json()["user"]["role"] == "user"
    assert c.post("/api/auth/login", json={"email": "visitor@example.com", "password": PW}).status_code == 403
    admin = make_client(app)
    assert admin.post("/api/auth/login", json={"email": "root@example.com", "password": PW}).status_code == 200
    assert admin.post("/api/repositories", json={"path": str(tmp_path)}).status_code == 400
    r = admin.post("/api/repositories", json={"git_url": "https://evil.example.com/a/b"})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]
    assert admin.get("/api/session").json()["local_paths_enabled"] is False
    assert admin.get("/api/docs").status_code == 404
    svc2.db.close()
