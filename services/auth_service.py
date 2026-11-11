"""User accounts: sign-up, login, sessions, roles and an audit trail.

Passwords: PBKDF2-HMAC-SHA256 with a per-user random salt (iteration count stored in the hash string).
Sessions: random 256-bit tokens; only their SHA-256 is stored. Tokens travel in an HttpOnly, SameSite=Strict cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from config.db import Database
from config.settings import Settings

ROLES = {"user", "admin"}
STATUSES = {"active", "pending", "disabled"}
MAX_FAILED = 5
LOCK_SECONDS = 15 * 60
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")
_COMMON = {"password", "password1", "password123", "1234567890", "123456789", "qwertyuiop", "letmein123", "iloveyou12",
           "admin12345", "welcome123", "changeme123", "codemind123"}


class AuthError(Exception):
    def __init__(self, status: int, kind: str, detail: str):
        super().__init__(detail)
        self.status, self.kind, self.detail = status, kind, detail


@dataclass
class User:
    id: str
    email: str
    display_name: str
    role: str
    status: str
    created_at: str = ""
    last_login_at: str | None = None
    must_change_password: bool = False

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "display_name": self.display_name or self.email.split("@")[0],
                "role": self.role, "status": self.status, "created_at": self.created_at,
                "last_login_at": self.last_login_at, "must_change_password": self.must_change_password}

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


LOCAL_ADMIN = User("local", "local@localhost", "Local operator", "admin", "active", "", None, False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str | None:
    if len(email) > 254 or not _EMAIL.match(email):
        return "Enter a valid email address."
    return None


def validate_password(pw: str, email: str = "") -> list[str]:
    errs = []
    if len(pw) < 10:
        errs.append("Use at least 10 characters.")
    if len(pw) > 128:
        errs.append("Use at most 128 characters.")
    if pw.isdigit() or pw.lower() in _COMMON or len(set(pw)) < 4:
        errs.append("That password is too easy to guess.")
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in pw.lower():
        errs.append("The password must not contain your email name.")
    return errs


def hash_password(pw: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, db: Database, settings: Settings, on_user_deleted: Callable[[str], None] | None = None):
        self.db, self.s, self.on_user_deleted = db, settings, on_user_deleted
        self._dummy = hash_password("dummy-password-for-timing", settings.password_iterations)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _user(r) -> User:
        return User(r["id"], r["email"], r["display_name"] or "", r["role"], r["status"], r["created_at"] or "",
                    r["last_login_at"], bool(r["must_change_password"]))

    def get(self, user_id: str) -> User | None:
        r = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        return self._user(r) if r else None

    def by_email(self, email: str):
        return self.db.one("SELECT * FROM users WHERE email=?", (normalize_email(email),))

    def count_users(self) -> int:
        return self.db.one("SELECT COUNT(*) c FROM users")["c"]

    def admin_count(self, active_only: bool = True) -> int:
        q = "SELECT COUNT(*) c FROM users WHERE role='admin'" + (" AND status='active'" if active_only else "")
        return self.db.one(q)["c"]

    def audit(self, actor: User | None, action: str, target: str = "", detail: str = "") -> None:
        self.db.execute("INSERT INTO audit_log(ts,actor_id,actor_email,action,target,detail) VALUES(?,?,?,?,?,?)",
                        (_now(), actor.id if actor else None, actor.email if actor else None, action, target[:200], detail[:300]))

    def _claim_orphans(self, user_id: str) -> None:
        self.db.execute("UPDATE repositories SET owner_id=? WHERE owner_id IS NULL", (user_id,))

    # ------------------------------------------------------------ sign-up / login
    def signup(self, email: str, password: str, display_name: str = "") -> User:
        email = normalize_email(email)
        err = validate_email(email)
        if err:
            raise AuthError(422, "invalid_email", err)
        errs = validate_password(password, email)
        if errs:
            raise AuthError(422, "weak_password", " ".join(errs))
        bootstrap = self.count_users() == 0 and not self.s.hosted
        if self.s.signup_mode == "closed" and not bootstrap:
            raise AuthError(403, "signup_closed", "Sign-ups are closed. Ask an administrator to create an account for you.")
        if self.by_email(email):
            raise AuthError(409, "email_taken", "An account with this email already exists.")
        role = "admin" if bootstrap else "user"
        status = "active" if (bootstrap or self.s.signup_mode == "open") else "pending"
        uid = uuid.uuid4().hex[:16]
        self.db.execute("INSERT INTO users(id,email,display_name,password_hash,role,status,created_at) VALUES(?,?,?,?,?,?,?)",
                        (uid, email, self._clean_name(display_name), hash_password(password, self.s.password_iterations), role, status, _now()))
        user = self.get(uid)
        if bootstrap:
            self._claim_orphans(uid)
        self.audit(user, "signup", email, f"role={role} status={status}")
        return user

    @staticmethod
    def _clean_name(name: str) -> str:
        return re.sub(r"[\x00-\x1f<>]", "", (name or "")).strip()[:60]

    def login(self, email: str, password: str, ip: str = "", ua: str = "") -> tuple[str, User]:
        row = self.by_email(email)
        if row is None:
            verify_password(password, self._dummy)  # equalise timing
            raise AuthError(401, "bad_credentials", "Incorrect email or password.")
        now = time.time()
        if (row["locked_until"] or 0) > now:
            mins = int(((row["locked_until"] - now) // 60) + 1)
            raise AuthError(429, "locked", f"Too many failed attempts. Try again in about {mins} minute(s).")
        if not verify_password(password, row["password_hash"]):
            failed = (row["failed_logins"] or 0) + 1
            lock = now + LOCK_SECONDS if failed >= MAX_FAILED else 0
            self.db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (0 if lock else failed, lock, row["id"]))
            self.audit(self._user(row), "login_failed", row["email"], f"ip={ip}" + (" (account locked)" if lock else ""))
            raise AuthError(401, "bad_credentials", "Incorrect email or password.")
        user = self._user(row)
        if user.status == "pending":
            raise AuthError(403, "pending", "Your account is waiting for administrator approval.")
        if user.status == "disabled":
            raise AuthError(403, "disabled", "This account has been disabled. Contact an administrator.")
        self.db.execute("UPDATE users SET failed_logins=0, locked_until=0, last_login_at=? WHERE id=?", (_now(), user.id))
        token = self._create_session(user.id, ip, ua)
        self.audit(user, "login", user.email, f"ip={ip}")
        return token, self.get(user.id)

    # ------------------------------------------------------------ sessions
    def _create_session(self, user_id: str, ip: str, ua: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute("INSERT INTO sessions(id,user_id,created_at,expires_at,last_seen,ip,user_agent) VALUES(?,?,?,?,?,?,?)",
                        (_tok_hash(token), user_id, now, now + self.s.session_ttl_hours * 3600, now, ip[:64], ua[:200]))
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return token

    def user_for_token(self, token: str | None) -> User | None:
        if not token or len(token) > 200:
            return None
        r = self.db.one("SELECT * FROM sessions WHERE id=?", (_tok_hash(token),))
        if r is None:
            return None
        now = time.time()
        if r["expires_at"] < now or now - (r["last_seen"] or 0) > self.s.session_idle_hours * 3600:
            self.db.execute("DELETE FROM sessions WHERE id=?", (r["id"],))
            return None
        user = self.get(r["user_id"])
        if user is None or user.status != "active":
            return None
        if now - (r["last_seen"] or 0) > 60:
            self.db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, r["id"]))
        return user

    def revoke(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE id=?", (_tok_hash(token),))

    def revoke_user_sessions(self, user_id: str, except_token: str | None = None) -> int:
        keep = _tok_hash(except_token) if except_token else ""
        n = self.db.one("SELECT COUNT(*) c FROM sessions WHERE user_id=? AND id<>?", (user_id, keep))["c"]
        self.db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (user_id, keep))
        return n

    def session_count(self, user_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM sessions WHERE user_id=? AND expires_at>?", (user_id, time.time()))["c"]

    # ------------------------------------------------------------ self-service
    def change_password(self, user: User, current: str, new: str, keep_token: str | None = None) -> None:
        row = self.db.one("SELECT password_hash FROM users WHERE id=?", (user.id,))
        if row is None or not verify_password(current, row["password_hash"]):
            raise AuthError(403, "bad_credentials", "Your current password is incorrect.")
        errs = validate_password(new, user.email)
        if errs:
            raise AuthError(422, "weak_password", " ".join(errs))
        if verify_password(new, row["password_hash"]):
            raise AuthError(422, "weak_password", "Choose a password you have not used just now.")
        self.db.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                        (hash_password(new, self.s.password_iterations), user.id))
        self.revoke_user_sessions(user.id, keep_token)
        self.audit(user, "password_changed", user.email)

    def update_profile(self, user: User, display_name: str) -> User:
        self.db.execute("UPDATE users SET display_name=? WHERE id=?", (self._clean_name(display_name), user.id))
        self.audit(user, "profile_updated", user.email)
        return self.get(user.id)

    # ------------------------------------------------------------ administration
    def list_users(self) -> list[User]:
        return [self._user(r) for r in self.db.query("SELECT * FROM users ORDER BY created_at DESC")]

    def _target(self, user_id: str) -> User:
        t = self.get(user_id)
        if t is None:
            raise AuthError(404, "not_found", "User not found.")
        return t

    def admin_update(self, actor: User, target_id: str, role: str | None = None, status: str | None = None) -> User:
        t = self._target(target_id)
        if role is not None and role not in ROLES or status is not None and status not in STATUSES:
            raise AuthError(422, "invalid", "Unknown role or status.")
        new_role, new_status = role or t.role, status or t.status
        if t.id == actor.id and (new_role != t.role or new_status != t.status):
            raise AuthError(409, "self_change", "You cannot change your own role or status.")
        if t.role == "admin" and t.status == "active" and (new_role != "admin" or new_status != "active") and self.admin_count() <= 1:
            raise AuthError(409, "last_admin", "At least one active administrator must remain.")
        self.db.execute("UPDATE users SET role=?, status=? WHERE id=?", (new_role, new_status, t.id))
        if new_status != "active":
            self.revoke_user_sessions(t.id)
        if new_role != t.role:
            self.audit(actor, "role_changed", t.email, f"{t.role} -> {new_role}")
        if new_status != t.status:
            self.audit(actor, {"active": "user_approved" if t.status == "pending" else "user_enabled", "disabled": "user_disabled",
                               "pending": "user_set_pending"}[new_status], t.email)
        return self.get(t.id)

    def reset_password(self, actor: User, target_id: str) -> str:
        t = self._target(target_id)
        temp = secrets.token_urlsafe(12) + "9a"
        self.db.execute("UPDATE users SET password_hash=?, must_change_password=1, failed_logins=0, locked_until=0 WHERE id=?",
                        (hash_password(temp, self.s.password_iterations), t.id))
        self.revoke_user_sessions(t.id)
        self.audit(actor, "password_reset", t.email)
        return temp

    def delete_user(self, actor: User, target_id: str) -> None:
        t = self._target(target_id)
        if t.id == actor.id:
            raise AuthError(409, "self_change", "You cannot delete your own account here.")
        if t.role == "admin" and t.status == "active" and self.admin_count() <= 1:
            raise AuthError(409, "last_admin", "At least one active administrator must remain.")
        if self.on_user_deleted:
            self.on_user_deleted(t.id)
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (t.id,))
        self.db.execute("DELETE FROM users WHERE id=?", (t.id,))
        self.audit(actor, "user_deleted", t.email)

    def audit_list(self, limit: int = 100, actor_id: str | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        if actor_id:
            rows = self.db.query("SELECT * FROM audit_log WHERE actor_id=? ORDER BY id DESC LIMIT ?", (actor_id, limit))
        else:
            rows = self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def seed_admin(self, email: str, password: str) -> User | None:
        """Create (or promote) the administrator named in the environment. Never overwrites an existing password."""
        email = normalize_email(email)
        if not email or not password:
            return None
        err = validate_email(email)
        if err:
            raise ValueError(f"CODEMIND_ADMIN_EMAIL: {err}")
        row = self.by_email(email)
        if row is None:
            errs = validate_password(password, email)
            if errs:
                raise ValueError("CODEMIND_ADMIN_PASSWORD: " + " ".join(errs))
            uid = uuid.uuid4().hex[:16]
            self.db.execute("INSERT INTO users(id,email,display_name,password_hash,role,status,created_at) VALUES(?,?,?,?,?,?,?)",
                            (uid, email, "Administrator", hash_password(password, self.s.password_iterations), "admin", "active", _now()))
            self.audit(None, "admin_seeded", email)
        else:
            self.db.execute("UPDATE users SET role='admin', status='active' WHERE id=?", (row["id"],))
            uid = row["id"]
        self._claim_orphans(uid)
        return self.get(uid)
