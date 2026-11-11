"""Decides which files may be indexed. Read-only; never executes anything."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from config.settings import Settings, load_exclusions
from parsing.languages import detect_language
from verification.safety_policy import find_secrets


@dataclass
class FileDecision:
    included: bool
    reason: str = ""
    sha256: str = ""
    size: int = 0
    mtime: float = 0.0
    language: str = ""
    encoding: str = "utf-8"
    lines: int = 0


def read_text_safe(path: Path, max_bytes: int | None = None) -> tuple[str, str]:
    """Read a file as text; returns (text, encoding). Falls back to latin-1 on decoding problems."""
    data = path.read_bytes() if max_bytes is None else path.read_bytes()[:max_bytes]
    return decode_bytes(data)


def decode_bytes(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1-fallback"


def _match_any(name: str, rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in patterns)


class FileFilter:
    def __init__(self, settings: Settings, exclusions: dict | None = None):
        self.s = settings
        ex = exclusions or load_exclusions()
        self.excluded_dirs = set(ex["excluded_dirs"])
        self.excluded_globs = ex["excluded_file_globs"]
        self.binary_ext = {e.lower() for e in ex["binary_extensions"]}
        self.secret_globs = ex["secret_filename_globs"]
        self.secret_allow = ex["secret_filename_allow"]

    # -- directories
    def dir_excluded(self, rel: str, name: str) -> str | None:
        if name in self.excluded_dirs or name.endswith(".egg-info"):
            return "excluded-dir"
        if self.s.exclude_patterns and _match_any(name, rel, self.s.exclude_patterns):
            return "user-exclude-pattern"
        return None

    # -- cheap pre-checks that do not read content
    def name_excluded(self, rel: str) -> str | None:
        name = PurePosixPath(rel).name
        if _match_any(name, rel, self.excluded_globs):
            return "excluded-file-pattern"
        if not _match_any(name, rel, self.secret_allow) and _match_any(name.lower(), rel.lower(), self.secret_globs):
            return "secret-bearing-filename"
        if PurePosixPath(rel).suffix.lower() in self.binary_ext:
            return "binary-extension"
        if self.s.exclude_patterns and _match_any(name, rel, self.s.exclude_patterns):
            return "user-exclude-pattern"
        if self.s.include_patterns and not _match_any(name, rel, self.s.include_patterns):
            return "not-in-include-patterns"
        return None

    def evaluate(self, abs_path: Path, rel: str) -> FileDecision:
        try:
            st = abs_path.lstat()
        except OSError:
            return FileDecision(False, "unreadable")
        if os.path.islink(abs_path):
            return FileDecision(False, "symlink")
        if not abs_path.is_file():
            return FileDecision(False, "not-a-regular-file")
        reason = self.name_excluded(rel)
        if reason:
            return FileDecision(False, reason)
        if st.st_size > self.s.max_file_bytes:
            return FileDecision(False, "oversized")
        try:
            data = abs_path.read_bytes()
        except OSError:
            return FileDecision(False, "unreadable")
        if b"\x00" in data[:8192]:
            return FileDecision(False, "binary-content")
        text, enc = decode_bytes(data)
        if any(h.confident for h in find_secrets(text)):
            return FileDecision(False, "possible-secret-content")
        lang = detect_language(rel)
        return FileDecision(True, "", hashlib.sha256(data).hexdigest(), st.st_size, st.st_mtime,
                            lang, enc, text.count("\n") + (0 if text.endswith("\n") or not text else 1))
