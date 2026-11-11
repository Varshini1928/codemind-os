"""File manifest: what was indexed, what was excluded and why, plus diffing between runs."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import Settings
from ingestion.file_filter import FileFilter


@dataclass
class Manifest:
    root: str
    files: dict[str, dict] = field(default_factory=dict)
    excluded: list[dict] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    total_bytes: int = 0

    def digest(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.files):
            h.update(f"{p}\0{self.files[p]['sha256']}\n".encode())
        return h.hexdigest()

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> "Manifest":
        return cls(**json.loads(raw))


@dataclass
class ManifestDiff:
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> list[str]:
        return self.added + self.modified


def diff_manifests(old: Manifest | None, new: Manifest) -> ManifestDiff:
    old_files = old.files if old else {}
    added = sorted(p for p in new.files if p not in old_files)
    modified = sorted(p for p in new.files if p in old_files and old_files[p]["sha256"] != new.files[p]["sha256"])
    deleted = sorted(p for p in old_files if p not in new.files)
    unchanged = sorted(p for p in new.files if p in old_files and old_files[p]["sha256"] == new.files[p]["sha256"])
    return ManifestDiff(added, modified, deleted, unchanged)


MAX_EXCLUDED_LISTED = 2000


def _note(m: Manifest, rel: str, reason: str) -> None:
    m.excluded_counts[reason] = m.excluded_counts.get(reason, 0) + 1
    if len(m.excluded) < MAX_EXCLUDED_LISTED:
        m.excluded.append({"path": rel, "reason": reason})


def build_manifest(root: Path, ff: FileFilter, settings: Settings) -> Manifest:
    """Walk the repository read-only. Symlinks are never followed."""
    m = Manifest(root=str(root))
    for cur, dirs, files in os.walk(root, topdown=True, followlinks=False):
        rel_dir = os.path.relpath(cur, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirs):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if os.path.islink(os.path.join(cur, d)):
                _note(m, rel, "symlink")
                continue
            reason = ff.dir_excluded(rel, d)
            if reason:
                _note(m, rel + "/", reason)
                continue
            keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if len(m.files) >= settings.max_files:
                m.truncated = True
                return m
            dec = ff.evaluate(Path(cur) / f, rel)
            if not dec.included:
                _note(m, rel, dec.reason)
                continue
            m.files[rel] = {"sha256": dec.sha256, "size": dec.size, "mtime": dec.mtime,
                            "language": dec.language, "encoding": dec.encoding, "lines": dec.lines}
            m.total_bytes += dec.size
    return m


def quick_signature(root: Path, ff: FileFilter, settings: Settings) -> str:
    """Stat-only fingerprint used to detect a stale index cheaply (no content hashing)."""
    h = hashlib.sha256()
    n = 0
    for cur, dirs, files in os.walk(root, topdown=True, followlinks=False):
        rel_dir = os.path.relpath(cur, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(cur, d))
                         and not ff.dir_excluded(f"{rel_dir}/{d}" if rel_dir else d, d))
        for f in sorted(files):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if ff.name_excluded(rel):
                continue
            try:
                st = os.lstat(os.path.join(cur, f))
            except OSError:
                continue
            if st.st_size > settings.max_file_bytes:
                continue
            n += 1
            if n > settings.max_files:
                return h.hexdigest()
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()
