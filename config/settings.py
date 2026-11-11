"""Central configuration. Every value can be overridden with a CODEMIND_<NAME> environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

import yaml

PREFIX = "CODEMIND_"
_EXCLUSIONS_FILE = Path(__file__).resolve().parent / "exclusions.yaml"

# Settings that the dashboard may change at runtime (persisted in SQLite).
# Security-relevant switches (writes, test execution, remote hosts) are environment-only on purpose.
RUNTIME_OVERRIDABLE = {
    "ollama_model", "ollama_timeout_s", "top_k", "max_graph_depth", "context_token_budget",
    "alpha", "beta", "gamma", "delta", "require_fresh_index", "strict_claims",
}


@dataclass
class Settings:
    storage_dir: Path = field(default_factory=lambda: Path.home() / ".codemind_os")
    clone_workspace: Path | None = None
    # --- local LLM (Ollama) ---
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_timeout_s: int = 180
    ollama_num_ctx: int = 8192
    allow_remote_ollama: bool = False
    # --- embeddings / vectors ---
    embedding_backend: str = "auto"  # auto | sentence-transformers | hashing
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    allow_model_download: bool = False
    vector_backend: str = "auto"  # auto | chroma | sqlite
    # --- indexing limits ---
    max_file_bytes: int = 1_000_000
    max_files: int = 20_000
    max_chunk_lines: int = 120
    min_free_disk_mb: int = 200
    include_patterns: list = field(default_factory=list)
    exclude_patterns: list = field(default_factory=list)
    # --- retrieval ---
    top_k: int = 12
    max_graph_depth: int = 2
    context_token_budget: int = 6000
    alpha: float = 0.45  # semantic similarity
    beta: float = 0.20   # graph relevance
    gamma: float = 0.30  # lexical / exact match
    delta: float = 0.05  # revision recency (file modification time)
    min_bm25: float = 1.0
    min_cosine: float = 0.30
    require_fresh_index: bool = False
    # --- workflow ---
    workflow_timeout_s: int = 300
    strict_claims: bool = False  # True: remove unsupported facts instead of labelling them
    # --- safety switches (default: read-only, nothing executed) ---
    allow_repo_writes: bool = False
    allow_test_execution: bool = False
    test_timeout_s: int = 120
    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    allow_network_bind: bool = False
    max_request_bytes: int = 1_000_000
    # --- hosted mode (e.g. Render): public deployment of the API + web UI ---
    hosted: bool = False
    # --- user accounts ---
    auth_mode: str = "accounts"  # accounts | none (none = single local operator, no login; refused when hosted)
    signup_mode: str = "approval"  # open | approval (admin must approve) | closed
    admin_email: str = ""  # seeds an administrator at startup (required when hosted)
    admin_password: str = ""  # never exposed by /settings
    session_ttl_hours: int = 168
    session_idle_hours: int = 24
    max_repos_per_user: int = 10  # 0 = unlimited; administrators are exempt
    cookie_secure: str = "auto"  # auto | true | false
    password_iterations: int = 600_000
    allowed_git_hosts: list = field(default_factory=list)
    max_clone_mb: int = 200

    # derived locations -------------------------------------------------
    @property
    def db_path(self) -> Path: return self.storage_dir / "codemind.db"
    @property
    def graphs_dir(self) -> Path: return self.storage_dir / "graphs"
    @property
    def vectors_dir(self) -> Path: return self.storage_dir / "vectors"
    @property
    def reports_dir(self) -> Path: return self.storage_dir / "reports"
    @property
    def review_dir(self) -> Path: return self.storage_dir / "review"
    @property
    def logs_dir(self) -> Path: return self.storage_dir / "logs"
    @property
    def models_dir(self) -> Path: return self.storage_dir / "models"

    def ensure_dirs(self) -> None:
        for d in (self.storage_dir, self.graphs_dir, self.vectors_dir, self.reports_dir,
                  self.review_dir, self.logs_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)

    def weights(self) -> dict[str, float]:
        return {"semantic": self.alpha, "graph": self.beta, "lexical": self.gamma, "recency": self.delta}

    def public_dict(self) -> dict[str, Any]:
        out = {f.name: str(getattr(self, f.name)) if isinstance(getattr(self, f.name), Path)
               else getattr(self, f.name) for f in fields(self)}
        out["admin_password"] = "(set)" if self.admin_password else "(not set)"
        out["storage_locations"] = {
            "database": str(self.db_path), "graphs": str(self.graphs_dir),
            "vectors": str(self.vectors_dir), "reports": str(self.reports_dir),
            "review_exports": str(self.review_dir), "logs": str(self.logs_dir),
            "model_cache": str(self.models_dir),
        }
        return out


def _coerce(raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(default, Path) or default is None:
        return Path(raw).expanduser()
    return raw


def load_settings(env: Mapping[str, str] | None = None, **overrides: Any) -> Settings:
    env = os.environ if env is None else env
    s = Settings()
    for f in fields(s):
        key = PREFIX + f.name.upper()
        if key in env:
            setattr(s, f.name, _coerce(env[key], getattr(s, f.name)))
    for k, v in overrides.items():
        if not hasattr(s, k):
            raise AttributeError(f"Unknown setting: {k}")
        setattr(s, k, v)
    s.storage_dir = Path(s.storage_dir).expanduser().resolve()
    if s.clone_workspace is not None:
        s.clone_workspace = Path(s.clone_workspace).expanduser().resolve()
    if "CODEMIND_SIGNUP_MODE" not in env and not s.hosted and "signup_mode" not in overrides:
        s.signup_mode = "open"  # local default; hosted default stays "approval"
    if s.auth_mode not in {"accounts", "none"} or s.signup_mode not in {"open", "approval", "closed"}:
        raise ValueError("auth_mode must be accounts|none and signup_mode must be open|approval|closed")
    if s.hosted:
        s.auth_mode = "accounts"
        # A public deployment never executes repository code or writes into repositories.
        s.allow_repo_writes = False
        s.allow_test_execution = False
        if s.clone_workspace is None:
            s.clone_workspace = s.storage_dir / "workspace"
        if "CODEMIND_ALLOWED_GIT_HOSTS" not in env and not overrides.get("allowed_git_hosts"):
            s.allowed_git_hosts = ["github.com", "gitlab.com", "bitbucket.org"]
    # Never allow telemetry from libraries we use.
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("DO_NOT_TRACK", "1")
    return s


_DEFAULT_EXCLUSIONS = {
    "excluded_dirs": [".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist"],
    "excluded_file_globs": ["*.min.js", "*.lock", "*.pyc"],
    "binary_extensions": [".png", ".jpg", ".pdf", ".zip", ".exe", ".dll", ".so"],
    "secret_filename_globs": [".env", ".env.*", "*.pem", "*.key", "id_rsa*"],
    "secret_filename_allow": [".env.example", ".env.sample", ".env.template"],
}


def load_exclusions(path: Path | None = None) -> dict[str, list[str]]:
    p = path or _EXCLUSIONS_FILE
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    merged = {k: list(v) for k, v in _DEFAULT_EXCLUSIONS.items()}
    for k, v in data.items():
        if isinstance(v, list):
            merged[k] = [str(x) for x in v]
    return merged
