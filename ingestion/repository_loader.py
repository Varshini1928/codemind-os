"""Repository acquisition: local directories, existing Git repositories, optional safe cloning.

Nothing here executes repository code, hooks, build scripts or package installers.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings
from ingestion.file_filter import FileFilter
from ingestion.manifest import Manifest, build_manifest, quick_signature
from verification.safety_policy import (SecurityError, scrubbed_env, validate_clone_destination,
                                        validate_git_url, validate_repository_path)


@dataclass
class RepoInfo:
    repo_id: str
    name: str
    root: Path
    is_git: bool
    head: str
    branch: str


class RepositoryLoader:
    def __init__(self, settings: Settings):
        self.s = settings
        self.filter = FileFilter(settings)

    # -- git (read-only, fixed argument lists, hooks/fsmonitor disabled, short timeout)
    def _git(self, root: Path, *args: str, timeout: int = 20) -> str | None:
        cmd = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-c", "safe.directory=" + str(root), "-C", str(root), *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=scrubbed_env())
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    def inspect(self, path: str) -> RepoInfo:
        root = validate_repository_path(path, self.s)
        head = self._git(root, "rev-parse", "HEAD") if (root / ".git").exists() else None
        branch = (self._git(root, "branch", "--show-current") or "") if head else ""
        rid = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "repo"
        rid = f"{rid[:24]}-{hashlib.sha1(str(root).encode()).hexdigest()[:8]}"
        return RepoInfo(rid, root.name, root, bool(head), head or "", branch)

    def build_manifest(self, root: Path) -> Manifest:
        return build_manifest(root, self.filter, self.s)

    def signature(self, root: Path) -> str:
        return quick_signature(root, self.filter, self.s)

    @staticmethod
    def revision_id(info: RepoInfo, manifest: Manifest) -> str:
        """Revision = Git HEAD (if any) + digest of indexed file contents, so uncommitted edits
        produce a different revision than the commit they are based on."""
        base = info.head[:12] if info.head else "nogit"
        return f"{base}+{manifest.digest()[:10]}"

    def clone(self, url: str, dest_name: str, timeout: int = 600) -> Path:
        url = validate_git_url(url, self.s.allowed_git_hosts, https_only=self.s.hosted)
        dest = validate_clone_destination(dest_name, self.s, allow_existing=True)
        if dest.exists() and (dest / ".git").exists():
            return dest  # already cloned (same deterministic folder name)
        cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never",
               "-c", "protocol.ext.allow=never", "clone", "--depth", "1", "--no-recurse-submodules", "--", url, str(dest)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=scrubbed_env())
        except (OSError, subprocess.TimeoutExpired) as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise SecurityError(f"git clone failed: {type(e).__name__}") from None
        if r.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise SecurityError("git clone failed (check the URL; private repositories are not supported on this deployment).")
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file() and not f.is_symlink())
        if size > self.s.max_clone_mb * 1024 * 1024:
            shutil.rmtree(dest, ignore_errors=True)
            raise SecurityError(f"Repository is larger than the {self.s.max_clone_mb} MB limit and was removed.")
        return dest
