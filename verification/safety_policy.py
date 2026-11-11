"""Central security policy: path safety, secret detection, approval gates, environment scrubbing."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from config.settings import Settings


class SecurityError(Exception):
    """Raised when an operation violates the security policy."""


class ApprovalRequired(SecurityError):
    """Raised when a potentially mutating operation lacks explicit approval or is disabled."""


# --------------------------------------------------------------------------- secrets
@dataclass(frozen=True)
class SecretHit:
    kind: str
    line: int
    confident: bool  # confident hits cause the whole file to be excluded from indexing


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


_CONFIDENT = [
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b")),
]
_GENERIC_KEY = re.compile(
    r"""(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*["']([A-Za-z0-9/+_\-=]{24,})["']""")
_PASSWORD = re.compile(r"""(?i)\b(password|passwd|pwd|secret|token)\b\s*[:=]\s*["']([^"'\n]{4,})["']""")


def find_secrets(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            line = line[:2000]
        for kind, rx in _CONFIDENT:
            if rx.search(line):
                hits.append(SecretHit(kind, lineno, True))
        m = _GENERIC_KEY.search(line)
        if m and _entropy(m.group(2)) >= 3.5:
            hits.append(SecretHit("high-entropy-credential-assignment", lineno, True))
        elif _PASSWORD.search(line):
            hits.append(SecretHit("password-like-assignment", lineno, False))
    return hits


def redact_secrets(text: str) -> str:
    """Replace secret-looking values with [REDACTED]. Used before logging and before prompting."""
    for _, rx in _CONFIDENT:
        text = rx.sub("[REDACTED]", text)
    text = _GENERIC_KEY.sub(lambda m: m.group(0).replace(m.group(2), "[REDACTED]"), text)
    text = _PASSWORD.sub(lambda m: m.group(0).replace(m.group(2), "[REDACTED]"), text)
    return text


# --------------------------------------------------------------------------- paths
_FORBIDDEN_ROOTS = {"/", "/etc", "/proc", "/sys", "/dev", "/boot", "/bin", "/sbin", "/usr", "/var", "/lib", "/root"}


def is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve a repository-relative path, refusing absolute paths, NUL bytes and traversal/symlink escapes."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise SecurityError("Invalid path.")
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or (len(rel) > 1 and rel[1] == ":") or ".." in p.parts:
        raise SecurityError("Path must be relative and must not contain '..'.")
    candidate = (root / Path(*p.parts)).resolve()
    if not is_within(root, candidate):
        raise SecurityError("Path resolves outside the repository root.")
    return candidate


def validate_repository_path(path: str, settings: Settings) -> Path:
    if not path or "\x00" in path:
        raise SecurityError("A repository path is required.")
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SecurityError(f"Path does not exist or cannot be resolved: {path}") from None
    if not p.is_dir():
        raise SecurityError("Repository path must be a directory.")
    if str(p) in _FORBIDDEN_ROOTS or p == Path(p.anchor) or p == Path.home().resolve():
        raise SecurityError("Refusing to index a filesystem root, system directory or your whole home directory.")
    if is_within(p, settings.storage_dir):
        raise SecurityError("The CodeMind storage directory lies inside this repository; choose another storage_dir.")
    if not os.access(p, os.R_OK | os.X_OK):
        raise SecurityError("Repository directory is not readable.")
    return p


_URL_OK = re.compile(r"^(https://[\w.\-]+(:\d+)?/[\w./~\-]+|ssh://[\w.\-@]+(:\d+)?/[\w./~\-]+|git@[\w.\-]+:[\w./~\-]+)$")


def validate_git_url(url: str, allowed_hosts: list[str] | None = None, https_only: bool = False) -> str:
    url = (url or "").strip()
    if not _URL_OK.match(url) or url.startswith("-"):
        raise SecurityError("Only https://, ssh:// or git@host:path URLs without embedded credentials are accepted.")
    if https_only and not url.startswith("https://"):
        raise SecurityError("Only https:// URLs are accepted on this deployment.")
    if allowed_hosts:
        host = re.split(r"[/:@]", url.split("://", 1)[-1].replace("git@", "", 1))[0].lower() if "://" in url \
            else url.split("@", 1)[-1].split(":", 1)[0].lower()
        if host not in {h.lower() for h in allowed_hosts}:
            raise SecurityError(f"Git host '{host}' is not allowed. Allowed: {', '.join(allowed_hosts)}.")
    return url


def validate_clone_destination(name: str, settings: Settings, allow_existing: bool = False) -> Path:
    if settings.clone_workspace is None:
        raise SecurityError("Cloning is disabled: set CODEMIND_CLONE_WORKSPACE to an explicit workspace directory.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}", name or ""):
        raise SecurityError("Destination name may only contain letters, digits, '.', '_' and '-'.")
    ws = settings.clone_workspace
    ws.mkdir(parents=True, exist_ok=True)
    dest = (ws / name).resolve()
    if not is_within(ws, dest) or dest == ws.resolve():
        raise SecurityError("Clone destination must be a new directory inside the clone workspace.")
    if dest.exists() and any(dest.iterdir()) and not (allow_existing and (dest / ".git").exists()):
        raise SecurityError("Clone destination already exists and is not empty.")
    return dest


# --------------------------------------------------------------------------- approvals & env
def require_approval(action: str, approved: bool, enabled: bool, flag_name: str) -> None:
    if not enabled:
        raise ApprovalRequired(
            f"'{action}' is disabled by default. Set {flag_name}=true in the environment to enable it, "
            "then confirm explicitly.")
    if approved is not True:
        raise ApprovalRequired(f"'{action}' requires explicit user approval (approved=true).")


_SAFE_ENV_KEYS = {"PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMP", "TEMP", "TMPDIR"}


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal environment for subprocesses: no tokens, keys or user variables are inherited."""
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    if extra:
        env.update(extra)
    return env


class RedactingFilter:
    """logging.Filter that removes secret-looking values from log records."""

    def filter(self, record) -> bool:  # pragma: no cover - trivial
        try:
            record.msg = redact_secrets(str(record.getMessage()))
            record.args = ()
        except Exception:
            pass
        return True
