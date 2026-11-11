"""SQLite access (metadata, jobs, chunk store, parse cache, settings overrides)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories(
  id TEXT PRIMARY KEY, name TEXT, root TEXT, git_url TEXT, branch TEXT, head TEXT, revision TEXT,
  status TEXT, indexed_at TEXT, signature TEXT, manifest_digest TEXT, file_count INTEGER DEFAULT 0,
  chunk_count INTEGER DEFAULT 0, entity_count INTEGER DEFAULT 0, embedder TEXT, embedder_dim INTEGER,
  warnings TEXT DEFAULT '[]', last_error TEXT, truncated INTEGER DEFAULT 0, created_at TEXT, owner_id TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, repo_id TEXT, kind TEXT, status TEXT, progress REAL DEFAULT 0, message TEXT DEFAULT '',
  logs TEXT DEFAULT '[]', error TEXT, result TEXT, created_at TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS manifests(repo_id TEXT PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS parses(repo_id TEXT, path TEXT, sha256 TEXT, json TEXT, PRIMARY KEY(repo_id, path));
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, repo_id TEXT, revision TEXT, path TEXT, language TEXT, symbol TEXT, kind TEXT,
  start_line INTEGER, end_line INTEGER, content_hash TEXT, entity_ids TEXT, text TEXT, file_mtime REAL,
  embedded INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_chunks_repo_path ON chunks(repo_id, path);
CREATE TABLE IF NOT EXISTS vectors(chunk_id TEXT PRIMARY KEY, repo_id TEXT, dim INTEGER, vec BLOB);
CREATE INDEX IF NOT EXISTS idx_vectors_repo ON vectors(repo_id);
CREATE TABLE IF NOT EXISTS settings_overrides(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT, last_login_at TEXT,
  failed_logins INTEGER DEFAULT 0, locked_until REAL DEFAULT 0, must_change_password INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL, expires_at REAL, last_seen REAL, ip TEXT, user_agent TEXT);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor_id TEXT, actor_email TEXT, action TEXT, target TEXT, detail TEXT);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(repositories)").fetchall()}
        if "owner_id" not in cols:  # databases created before user accounts existed
            self.conn.execute("ALTER TABLE repositories ADD COLUMN owner_id TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_owner ON repositories(owner_id)")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    @contextmanager
    def transaction(self):
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                yield self
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def integrity_ok(self) -> bool:
        try:
            return self.query("PRAGMA integrity_check")[0][0] == "ok"
        except sqlite3.DatabaseError:
            return False

    def close(self) -> None:
        with self._lock:
            self.conn.close()
