"""Background jobs (single worker thread) persisted in SQLite with progress, logs and cancellation."""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from config.db import Database
from verification.safety_policy import redact_secrets


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(Exception):
    pass


class JobService:
    def __init__(self, db: Database):
        self.db = db
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codemind-job")
        self.events: dict[str, threading.Event] = {}
        self.futures: dict[str, object] = {}
        # Jobs that were running when the process died can never finish: mark them honestly.
        self.db.execute("UPDATE jobs SET status='failed', error='Interrupted by application restart', finished_at=? "
                        "WHERE status IN ('queued','running')", (now(),))

    def submit(self, repo_id: str, kind: str, fn: Callable[[Callable[[float, str], None], threading.Event], dict]) -> str:
        jid = uuid.uuid4().hex[:12]
        ev = threading.Event()
        self.events[jid] = ev
        self.db.execute("INSERT INTO jobs(id,repo_id,kind,status,created_at) VALUES(?,?,?,?,?)",
                        (jid, repo_id, kind, "queued", now()))

        def progress(frac: float, msg: str) -> None:
            row = self.db.one("SELECT logs FROM jobs WHERE id=?", (jid,))
            logs = json.loads(row["logs"]) if row else []
            logs.append(f"{now()} {redact_secrets(msg)}"[:500])
            self.db.execute("UPDATE jobs SET progress=?, message=?, logs=? WHERE id=?",
                            (round(frac, 3), redact_secrets(msg)[:300], json.dumps(logs[-300:]), jid))

        def run() -> None:
            if ev.is_set():
                self.db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (now(), jid))
                return
            self.db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now(), jid))
            try:
                result = fn(progress, ev)
                self.db.execute("UPDATE jobs SET status='succeeded', progress=1, result=?, finished_at=? WHERE id=?",
                                (json.dumps(result, default=str), now(), jid))
            except JobCancelled:
                self.db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (now(), jid))
            except Exception as e:  # noqa: BLE001 - job boundary
                self.db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                                (redact_secrets(f"{type(e).__name__}: {e}")[:1000], now(), jid))

        self.futures[jid] = self.pool.submit(run)
        return jid

    def get(self, jid: str) -> dict | None:
        r = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        return self._row(r) if r else None

    def list(self, repo_id: str | None = None, limit: int = 30) -> list[dict]:
        if repo_id:
            rows = self.db.query("SELECT * FROM jobs WHERE repo_id=? ORDER BY created_at DESC LIMIT ?", (repo_id, limit))
        else:
            rows = self.db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def cancel(self, jid: str) -> bool:
        ev = self.events.get(jid)
        job = self.get(jid)
        if not ev or not job or job["status"] not in {"queued", "running"}:
            return False
        ev.set()
        return True

    def wait(self, jid: str, timeout: float | None = None) -> dict | None:
        fut = self.futures.get(jid)
        if fut is not None:
            try:
                fut.result(timeout)  # type: ignore[attr-defined]
            except Exception:
                pass
        return self.get(jid)

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["logs"] = json.loads(d.get("logs") or "[]")
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d
