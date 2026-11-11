"""Response builders shared by the API and the UI fixture generator."""
from __future__ import annotations

from pathlib import Path


def _dir_size(p: Path, cap: int = 20000) -> int:
    n = total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
            n += 1
            if n >= cap:
                break
    return total


def repo_row(svc, r: dict, owners: dict) -> dict:
    o = owners.get(r.get("owner_id"))
    return {**r, "owner_email": o.email if o else None, "owner_name": (o.display_name or o.email) if o else None}


def me_payload(svc, user) -> dict:
    repos = svc.indexing.list_repos(user.id) if user.id != "local" else svc.indexing.list_repos()
    reports = sum(len(svc.reports.list(r["id"])) for r in repos)
    return {"user": user.public(), "sessions": svc.auth.session_count(user.id) if user.id != "local" else 0,
            "stats": {"repositories": len(repos), "ready": sum(r["status"] == "ready" for r in repos), "reports": reports,
                      "files": sum(r["file_count"] or 0 for r in repos), "chunks": sum(r["chunk_count"] or 0 for r in repos)},
            "repositories": [{"id": r["id"], "name": r["name"], "status": r["status"], "indexed_at": r["indexed_at"]} for r in repos],
            "activity": svc.auth.audit_list(15, user.id) if user.id != "local" else []}


def admin_users(svc) -> list[dict]:
    counts = {r["owner_id"]: r["c"] for r in svc.db.query("SELECT owner_id, COUNT(*) c FROM repositories GROUP BY owner_id")}
    return [{**u.public(), "repositories": counts.get(u.id, 0), "sessions": svc.auth.session_count(u.id)} for u in svc.auth.list_users()]


def admin_repos(svc) -> list[dict]:
    owners = {u.id: u for u in svc.auth.list_users()}
    return [repo_row(svc, r, owners) for r in svc.indexing.list_repos()]


def admin_stats(svc) -> dict:
    users = svc.auth.list_users()
    repos = svc.indexing.list_repos()
    jobs = svc.db.query("SELECT status, COUNT(*) c FROM jobs GROUP BY status")
    return {"users": {"total": len(users), "active": sum(u.status == "active" for u in users), "pending": sum(u.status == "pending" for u in users),
                      "disabled": sum(u.status == "disabled" for u in users), "admins": sum(u.role == "admin" for u in users)},
            "repositories": {"total": len(repos), "ready": sum(r["status"] == "ready" for r in repos),
                             "chunks": sum(r["chunk_count"] or 0 for r in repos), "unowned": sum(r.get("owner_id") is None for r in repos)},
            "jobs": {r["status"]: r["c"] for r in jobs}, "reports": len(svc.reports.list()),
            "storage_bytes": _dir_size(svc.settings.storage_dir), "signup_mode": svc.settings.signup_mode,
            "hosted": svc.settings.hosted}
