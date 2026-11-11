import sqlite3
import time

import pytest

from config.db import Database
from services.auth_service import AuthError, hash_password, validate_email, validate_password, verify_password
from verification.safety_policy import SecurityError

PW = "correct horse 42"
PW2 = "another phrase 77"


def _err(fn, *a, **k):
    with pytest.raises(AuthError) as e:
        fn(*a, **k)
    return e.value


def test_password_hashing():
    h = hash_password("s3cret-phrase", 1000)
    assert h.startswith("pbkdf2_sha256$1000$") and "s3cret" not in h
    assert verify_password("s3cret-phrase", h) and not verify_password("nope", h)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$a$b")
    assert hash_password("s3cret-phrase", 1000) != h  # random salt


def test_policies():
    assert validate_email("a@b.co") is None and validate_email("nope") and validate_email("a@b") and validate_email("a b@c.com")
    assert validate_password(PW, "x@y.com") == []
    assert validate_password("short1", "") and validate_password("1234567890", "") and validate_password("x" * 200, "")
    assert validate_password("alexander-rocks-1", "alexander@example.com")  # contains email name
    assert validate_password("aaaaaaaaaaaa", "")  # too few distinct characters


def test_first_user_is_admin_then_modes(services):
    a = services.auth
    first = a.signup("Boss@Example.com", PW, "Boss <b>")
    assert (first.role, first.status, first.email, first.display_name) == ("admin", "active", "boss@example.com", "Boss b")
    services.settings.signup_mode = "approval"
    u = a.signup("u@example.com", PW)
    assert (u.role, u.status) == ("user", "pending")
    assert _err(a.login, "u@example.com", PW).kind == "pending"
    services.settings.signup_mode = "open"
    assert a.signup("o@example.com", PW).status == "active"
    services.settings.signup_mode = "closed"
    assert _err(a.signup, "c@example.com", PW).kind == "signup_closed"
    assert _err(a.signup, "BOSS@example.com", PW).status in (403, 409)


def test_duplicates_and_validation(services):
    a = services.auth
    a.signup("x@example.com", PW)
    assert _err(a.signup, "X@EXAMPLE.COM", PW).kind == "email_taken"
    assert _err(a.signup, "bad-email", PW).kind == "invalid_email"
    assert _err(a.signup, "y@example.com", "short").kind == "weak_password"


def test_hosted_never_bootstraps_first_visitor_as_admin(services):
    services.settings.hosted = True
    services.settings.signup_mode = "approval"  # the hosted default
    u = services.auth.signup("first@example.com", PW)
    assert (u.role, u.status) == ("user", "pending")


def test_login_generic_errors_and_lockout(services):
    a = services.auth
    a.signup("u@example.com", PW)
    e1, e2 = _err(a.login, "nobody@example.com", PW), _err(a.login, "u@example.com", "wrong password 1")
    assert e1.detail == e2.detail and e1.status == e2.status == 401
    for _ in range(4):
        _err(a.login, "u@example.com", "wrong password 1")
    assert _err(a.login, "u@example.com", PW).kind == "locked"  # 5th failure locked the account, even the right password fails
    services.db.execute("UPDATE users SET locked_until=0 WHERE email='u@example.com'")
    token, user = a.login("u@example.com", PW)
    assert token and user.last_login_at


def test_sessions(services):
    a = services.auth
    a.signup("u@example.com", PW)
    token, user = a.login("u@example.com", PW, "1.2.3.4", "pytest")
    assert a.user_for_token(token).id == user.id and a.user_for_token("bogus") is None and a.user_for_token(None) is None
    raw = services.db.query("SELECT id FROM sessions")[0]["id"]
    assert raw != token and token not in raw  # only the hash is stored
    services.db.execute("UPDATE sessions SET last_seen=?", (time.time() - 48 * 3600,))
    assert a.user_for_token(token) is None  # idle timeout
    token, _ = a.login("u@example.com", PW)
    services.db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert a.user_for_token(token) is None  # absolute expiry
    token, _ = a.login("u@example.com", PW)
    a.revoke(token)
    assert a.user_for_token(token) is None


def test_change_password(services):
    a = services.auth
    u = a.signup("u@example.com", PW)
    t1, _ = a.login("u@example.com", PW)
    t2, _ = a.login("u@example.com", PW)
    assert _err(a.change_password, u, "wrong", PW2).kind == "bad_credentials"
    assert _err(a.change_password, u, PW, "short").kind == "weak_password"
    a.change_password(u, PW, PW2, keep_token=t1)
    assert a.user_for_token(t1) and a.user_for_token(t2) is None
    assert _err(a.login, "u@example.com", PW).kind == "bad_credentials" and a.login("u@example.com", PW2)


def test_admin_operations(services):
    a = services.auth
    admin = a.signup("admin@example.com", PW)
    services.settings.signup_mode = "approval"
    u = a.signup("u@example.com", PW)
    approved = a.admin_update(admin, u.id, status="active")
    assert approved.status == "active" and a.login("u@example.com", PW)
    token, _ = a.login("u@example.com", PW)
    a.admin_update(admin, u.id, status="disabled")
    assert a.user_for_token(token) is None and _err(a.login, "u@example.com", PW).kind == "disabled"
    assert _err(a.admin_update, admin, admin.id, role="user").kind == "self_change"
    admin2 = a.admin_update(admin, a.signup("a2@example.com", PW).id, role="admin", status="active")
    assert a.admin_count() == 2
    a.admin_update(admin, admin2.id, status="disabled")
    assert _err(a.admin_update, admin2, admin.id, status="disabled").kind in {"last_admin", "self_change"}
    assert _err(a.delete_user, admin2, admin.id).kind in {"last_admin", "self_change"}
    assert _err(a.admin_update, admin, u.id, role="root").kind == "invalid"
    assert _err(a.admin_update, admin, "nope", role="user").status == 404
    actions = [r["action"] for r in a.audit_list(50)]
    assert {"signup", "user_approved", "user_disabled", "role_changed"} <= set(actions)


def test_last_admin_cannot_be_removed(services):
    a = services.auth
    admin = a.signup("admin@example.com", PW)
    other = a.admin_update(admin, a.signup("o@example.com", PW).id, role="admin")
    a.admin_update(admin, other.id, status="disabled")  # leaves exactly one active administrator
    assert _err(a.admin_update, other, admin.id, role="user").kind == "last_admin"
    assert _err(a.admin_update, other, admin.id, status="disabled").kind == "last_admin"
    assert _err(a.delete_user, other, admin.id).kind == "last_admin"


def test_reset_password_forces_change(services):
    a = services.auth
    admin = a.signup("admin@example.com", PW)
    u = a.signup("u@example.com", PW)
    t, _ = a.login("u@example.com", PW)
    temp = a.reset_password(admin, u.id)
    assert a.user_for_token(t) is None and temp != PW
    _, logged = a.login("u@example.com", temp)
    assert logged.must_change_password is True
    a.change_password(logged, temp, PW2)
    assert a.get(u.id).must_change_password is False


def test_delete_user_purges_their_repositories(services, sample_repo):
    a = services.auth
    admin = a.signup("admin@example.com", PW)
    u = a.signup("u@example.com", PW)
    repo = services.indexing.register(str(sample_repo), owner_id=u.id)
    services.indexing.index_now(repo["id"])
    a.delete_user(admin, u.id)
    assert services.indexing.get_repo(repo["id"]) is None and a.get(u.id) is None
    assert services.chunks.count(repo["id"]) == 0


def test_seed_admin(services):
    a = services.auth
    first = a.seed_admin("Root@Example.com", PW)
    assert (first.role, first.status) == ("admin", "active")
    again = a.seed_admin("root@example.com", "a different pass 99")
    assert again.id == first.id and a.login("root@example.com", PW)  # password untouched
    with pytest.raises(ValueError):
        a.seed_admin("new@example.com", "weak")
    assert a.seed_admin("", "") is None


def test_repository_ownership(services, tmp_path):
    root = tmp_path / "r"; root.mkdir(); (root / "a.py").write_text("x=1\n")
    r = services.indexing.register(str(root), owner_id="u1")
    assert services.indexing.register(str(root), owner_id="u1")["id"] == r["id"]
    with pytest.raises(SecurityError, match="another account"):
        services.indexing.register(str(root), owner_id="u2")
    assert [x["id"] for x in services.indexing.list_repos("u1")] == [r["id"]] and services.indexing.list_repos("u2") == []
    assert services.indexing.count_owned("u1") == 1
    from services.indexing_service import IndexingService as I
    assert I._clone_name("https://github.com/o/r", "u1") != I._clone_name("https://github.com/o/r", "u2")


def test_orphan_repos_claimed_by_first_admin(services, tmp_path):
    root = tmp_path / "r"; root.mkdir(); (root / "a.py").write_text("x=1\n")
    r = services.indexing.register(str(root))  # legacy: no owner
    admin = services.auth.signup("admin@example.com", PW)
    assert services.indexing.get_repo(r["id"])["owner_id"] == admin.id


def test_database_migration_adds_owner_column(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE repositories(id TEXT PRIMARY KEY, name TEXT, root TEXT)")
    con.execute("INSERT INTO repositories VALUES('a','a','/x')")
    con.commit(); con.close()
    db = Database(p)
    cols = {r["name"] for r in db.query("PRAGMA table_info(repositories)")}
    assert "owner_id" in cols and db.query("SELECT id FROM repositories")[0]["id"] == "a"
    db.close()


def test_views(services):
    from backend import views
    admin = services.auth.signup("admin@example.com", PW)
    services.auth.signup("u@example.com", PW)
    st = views.admin_stats(services)
    assert st["users"]["total"] == 2 and st["users"]["admins"] == 1
    assert len(views.admin_users(services)) == 2 and views.me_payload(services, admin)["user"]["role"] == "admin"
