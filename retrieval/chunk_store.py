"""SQLite-backed chunk storage (text + metadata). ChromaDB/SQLite vectors reference chunks by id."""
from __future__ import annotations

import json

from config.db import Database
from retrieval.chunker import Chunk


def _row(r) -> dict:
    d = dict(r)
    d["entity_ids"] = json.loads(d.get("entity_ids") or "[]")
    return d


class ChunkStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, chunks: list[Chunk]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO chunks(id,repo_id,revision,path,language,symbol,kind,start_line,end_line,"
            "content_hash,entity_ids,text,file_mtime,embedded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            [(c.id, c.repo_id, c.revision, c.path, c.language, c.symbol, c.kind, c.start_line, c.end_line,
              c.content_hash, json.dumps(c.entity_ids), c.text, c.file_mtime) for c in chunks])

    def ids_for_paths(self, repo_id: str, paths: list[str]) -> list[str]:
        out: list[str] = []
        for p in paths:
            out += [r["id"] for r in self.db.query("SELECT id FROM chunks WHERE repo_id=? AND path=?", (repo_id, p))]
        return out

    def delete_paths(self, repo_id: str, paths: list[str]) -> list[str]:
        ids = self.ids_for_paths(repo_id, paths)
        for p in paths:
            self.db.execute("DELETE FROM chunks WHERE repo_id=? AND path=?", (repo_id, p))
        return ids

    def delete_repo(self, repo_id: str) -> None:
        self.db.execute("DELETE FROM chunks WHERE repo_id=?", (repo_id,))

    def set_revision(self, repo_id: str, revision: str) -> None:
        self.db.execute("UPDATE chunks SET revision=? WHERE repo_id=?", (revision, repo_id))

    def get(self, chunk_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
        return _row(r) if r else None

    def all_for_repo(self, repo_id: str) -> list[dict]:
        return [_row(r) for r in self.db.query("SELECT * FROM chunks WHERE repo_id=? ORDER BY path,start_line", (repo_id,))]

    def for_path(self, repo_id: str, path: str) -> list[dict]:
        return [_row(r) for r in self.db.query(
            "SELECT * FROM chunks WHERE repo_id=? AND path=? ORDER BY start_line", (repo_id, path))]

    def chunk_for_line(self, repo_id: str, path: str, line: int) -> dict | None:
        r = self.db.one("SELECT * FROM chunks WHERE repo_id=? AND path=? AND start_line<=? AND end_line>=?",
                        (repo_id, path, line, line))
        return _row(r) if r else None

    def file_lines(self, repo_id: str, path: str, start: int | None = None, end: int | None = None) -> list[str]:
        """Reassemble indexed file lines (blank lines between chunks come back empty)."""
        chunks = self.for_path(repo_id, path)
        if not chunks:
            return []
        last = max(c["end_line"] for c in chunks)
        lines = [""] * last
        for c in chunks:
            for i, ln in enumerate(c["text"].split("\n")):
                if c["start_line"] - 1 + i < last:
                    lines[c["start_line"] - 1 + i] = ln
        if start is None:
            return lines
        return lines[max(start, 1) - 1:end]

    def file_text(self, repo_id: str, path: str) -> str:
        return "\n".join(self.file_lines(repo_id, path))

    def paths(self, repo_id: str) -> list[str]:
        return [r["path"] for r in self.db.query("SELECT DISTINCT path FROM chunks WHERE repo_id=? ORDER BY path", (repo_id,))]

    def count(self, repo_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM chunks WHERE repo_id=?", (repo_id,))["c"]

    def pending_embedding(self, repo_id: str, limit: int = 64) -> list[dict]:
        return [_row(r) for r in self.db.query(
            "SELECT * FROM chunks WHERE repo_id=? AND embedded=0 LIMIT ?", (repo_id, limit))]

    def pending_count(self, repo_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM chunks WHERE repo_id=? AND embedded=0", (repo_id,))["c"]

    def mark_embedded(self, ids: list[str]) -> None:
        self.db.executemany("UPDATE chunks SET embedded=1 WHERE id=?", [(i,) for i in ids])

    def reset_embedded(self, repo_id: str) -> None:
        self.db.execute("UPDATE chunks SET embedded=0 WHERE repo_id=?", (repo_id,))
