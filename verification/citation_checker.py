"""Citation validation. A valid citation proves the cited lines exist in the indexed revision - it does NOT
prove that a natural-language claim about them is correct (see evidence_validator)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from retrieval.chunk_store import ChunkStore
from verification.safety_policy import SecurityError, resolve_inside

CITATION_RX = re.compile(r"(?P<path>[\w][\w./\\-]*\.\w{1,6}):(?P<s>\d+)-(?P<e>\d+)(?:@(?P<rev>[0-9a-f]+\+[0-9a-f]+))?")


@dataclass
class Citation:
    revision: str
    path: str
    start_line: int
    end_line: int
    symbol: str = ""
    chunk_id: str = ""


@dataclass
class CitationResult:
    citation: dict
    valid: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"citation": self.citation, "valid": self.valid, "checks": self.checks, "reason": self.reason,
                "semantic_correctness": "not verified"}


class CitationChecker:
    def __init__(self, repo_id: str, root: Path | None, indexed_revision: str, chunks: ChunkStore):
        self.repo_id, self.root, self.rev, self.chunks = repo_id, root, indexed_revision, chunks

    def check(self, c: Citation, verify_on_disk: bool = True) -> CitationResult:
        checks = {"path_in_repository": False, "line_range_valid": False, "revision_matches": False,
                  "chunk_matches_source": False}
        cd = c.__dict__.copy()
        reason = ""
        # 1. path exists inside the indexed repository (and is an indexed, non-excluded file)
        try:
            if self.root is not None:
                p = resolve_inside(self.root, c.path)
                exists_on_disk = p.is_file()
            else:
                exists_on_disk = True
            indexed = bool(self.chunks.for_path(self.repo_id, c.path))
            checks["path_in_repository"] = exists_on_disk and indexed
            if not indexed:
                reason = "path is not part of the index (missing, excluded or never indexed)"
            elif not exists_on_disk:
                reason = "file no longer exists on disk"
        except SecurityError as e:
            reason = f"path rejected: {e}"
        # 2. line range valid w.r.t. indexed lines
        if checks["path_in_repository"]:
            lines = self.chunks.file_lines(self.repo_id, c.path)
            checks["line_range_valid"] = 1 <= c.start_line <= c.end_line <= len(lines)
            if not checks["line_range_valid"]:
                reason = f"line range {c.start_line}-{c.end_line} outside file ({len(lines)} lines)"
        # 3. revision
        checks["revision_matches"] = (c.revision == self.rev)
        if not checks["revision_matches"]:
            reason = reason or f"citation revision '{c.revision}' differs from indexed revision '{self.rev}'"
        # 4. chunk corresponds to the referenced source
        if checks["line_range_valid"]:
            ch = self.chunks.get(c.chunk_id) if c.chunk_id else self.chunks.chunk_for_line(self.repo_id, c.path, c.start_line)
            if ch is None:
                reason = reason or "chunk id not found"
            elif ch["path"] != c.path or ch["end_line"] < c.start_line or ch["start_line"] > c.end_line:
                reason = reason or "chunk does not cover the cited lines"
            else:
                ok = True
                if verify_on_disk and self.root is not None:
                    try:
                        disk = resolve_inside(self.root, c.path).read_text(encoding="utf-8", errors="replace").splitlines()
                        chunk_lines = ch["text"].split("\n")
                        seg = disk[ch["start_line"] - 1:ch["end_line"]]
                        ok = [x.rstrip() for x in seg] == [x.rstrip() for x in chunk_lines]
                        if not ok:
                            reason = reason or "file content on disk differs from the indexed chunk (stale index)"
                    except OSError:
                        ok = False
                        reason = reason or "file could not be read for verification"
                checks["chunk_matches_source"] = ok
        valid = all(checks.values())
        return CitationResult(cd, valid, checks, "" if valid else reason)

    def find_inline(self, text: str) -> list[Citation]:
        return [Citation(m.group("rev") or "", m.group("path").replace("\\", "/"), int(m.group("s")), int(m.group("e")))
                for m in CITATION_RX.finditer(text)]
