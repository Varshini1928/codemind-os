"""Opt-in test execution. Disabled by default; needs CODEMIND_ALLOW_TEST_EXECUTION=true AND approved=true.

Isolation actually implemented: a copy of the indexed files in a temporary directory (the original repository is not
touched), a scrubbed environment (no tokens/keys inherited), a wall-clock timeout and POSIX resource limits (CPU, memory,
file size). This is NOT a security sandbox: tests run with your user's privileges and can still use the network and read
files you can read. Run untrusted repositories inside a VM or container instead.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from config.settings import Settings
from ingestion.manifest import Manifest
from verification.safety_policy import redact_secrets, require_approval, scrubbed_env

ISOLATION_NOTE = ("Ran in a temporary copy with a scrubbed environment, timeout and resource limits. This is not a secure "
                  "sandbox: code can still access the network and any file your user can read.")


def _limits():  # pragma: no cover - runs in the child process
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 << 20, 100 << 20))
    except Exception:
        pass


def run_tests(settings: Settings, repo_root: Path, manifest: Manifest, approved: bool,
              command: list[str] | None = None) -> dict:
    require_approval("execute repository tests", approved, settings.allow_test_execution, "CODEMIND_ALLOW_TEST_EXECUTION")
    cmd = command or [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"]
    tmp = Path(tempfile.mkdtemp(prefix="codemind_run_"))
    try:
        for rel in manifest.files:
            src, dst = repo_root / rel, tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=settings.test_timeout_s,
                               env=scrubbed_env({"HOME": str(tmp)}), preexec_fn=_limits if sys.platform != "win32" else None)
            res = {"returncode": r.returncode, "stdout": redact_secrets(r.stdout[-8000:]), "stderr": redact_secrets(r.stderr[-4000:]),
                   "timed_out": False}
        except subprocess.TimeoutExpired:
            res = {"returncode": None, "stdout": "", "stderr": "", "timed_out": True}
        res.update(duration_s=round(time.time() - t0, 2), command=cmd, isolation=ISOLATION_NOTE)
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
