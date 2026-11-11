import socket
import sys
from pathlib import Path

import pytest

from ingestion.manifest import Manifest
from retrieval.hybrid_search import IndexNotReadyError
from services.report_service import ReportService
from services.test_execution import run_tests
from verification.safety_policy import (ApprovalRequired, SecurityError, find_secrets, redact_secrets, resolve_inside,
                                        scrubbed_env, validate_clone_destination, validate_git_url)


def test_path_traversal_prevented(tmp_path):
    root = tmp_path / "r"; root.mkdir()
    (root / "a.txt").write_text("x")
    assert resolve_inside(root, "a.txt").name == "a.txt"
    for bad in ["../x", "a/../../x", "/etc/passwd", "C:\\Windows", "", "a\x00b"]:
        with pytest.raises(SecurityError):
            resolve_inside(root, bad)


def test_symlink_escape_prevented(tmp_path):
    root, out = tmp_path / "r", tmp_path / "o"; root.mkdir(); out.mkdir()
    try:
        (root / "l").symlink_to(out)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(SecurityError):
        resolve_inside(root, "l/file.txt")


def test_secret_detection_and_redaction():
    text = 'a = 1\nkey = "AKIAABCDEFGHIJKLMNOP"\npassword = "hunter2-prod"\n-----BEGIN RSA PRIVATE KEY-----\n'
    hits = find_secrets(text)
    assert {h.kind for h in hits} == {"aws-access-key-id", "password-like-assignment", "private-key-block"}
    red = redact_secrets(text)
    assert "AKIA" not in red and "hunter2" not in red


def test_git_url_and_clone_destination(settings, tmp_path):
    for bad in ["file:///etc", "--upload-pack=x", "https://user:pw@host/x.git", "ext::sh -c id", "http://x/y", ""]:
        with pytest.raises(SecurityError):
            validate_git_url(bad)
    assert validate_git_url("https://github.com/org/repo.git")
    with pytest.raises(SecurityError):
        validate_clone_destination("x", settings)  # no workspace configured
    settings.clone_workspace = tmp_path / "ws"
    assert validate_clone_destination("repo-1", settings).parent == (tmp_path / "ws").resolve()
    for bad in ["../evil", "a/b", ".", ""]:
        with pytest.raises(SecurityError):
            validate_clone_destination(bad, settings)


def test_scrubbed_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "abc")
    assert "MY_API_TOKEN" not in scrubbed_env()


def test_writes_disabled_by_default(settings, tmp_path):
    root = tmp_path / "r"; root.mkdir()
    rs = ReportService(settings)
    with pytest.raises(ApprovalRequired):
        rs.write_into_repository(root, "docs/x.md", "hi", approved=True)
    settings.allow_repo_writes = True
    with pytest.raises(ApprovalRequired):
        rs.write_into_repository(root, "docs/x.md", "hi", approved=False)
    assert Path(rs.write_into_repository(root, "docs/x.md", "hi", approved=True)).read_text() == "hi"
    with pytest.raises(SecurityError):  # never overwrite
        rs.write_into_repository(root, "docs/x.md", "again", approved=True)
    with pytest.raises(SecurityError):
        rs.write_into_repository(root, "../escape.md", "x", approved=True)


def test_test_execution_gated_and_scrubbed(indexed, settings, monkeypatch):
    svc, rid, root = indexed
    manifest = Manifest.from_json(svc.db.one("SELECT json FROM manifests WHERE repo_id=?", (rid,))["json"])
    with pytest.raises(ApprovalRequired):
        run_tests(settings, root, manifest, approved=True)
    settings.allow_test_execution = True
    with pytest.raises(ApprovalRequired):
        run_tests(settings, root, manifest, approved=False)
    monkeypatch.setenv("LEAK_TOKEN", "topsecret")
    cmd = [sys.executable, "-c", "import os,sys;print('LEAK' if 'LEAK_TOKEN' in os.environ else 'clean', os.getcwd() != %r)" % str(root)]
    res = run_tests(settings, root, manifest, approved=True, command=cmd)
    assert res["returncode"] == 0 and res["stdout"].split()[0] == "clean" and "not a secure sandbox" in res["isolation"]
    assert not (root / "__pycache__").exists()


def test_report_id_validation(settings):
    rs = ReportService(settings)
    for bad in ("../x", "a/b", ""):
        with pytest.raises(SecurityError):
            rs.read("repo", bad)


def test_indexing_and_queries_make_no_external_connections(services, sample_repo, monkeypatch):
    """Local-only check: any non-loopback socket connection during indexing/inference fails the test."""
    attempts = []
    real_connect = socket.socket.connect

    def guard(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else str(address)
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in {"127.0.0.1", "::1", "localhost"}:
            attempts.append(host)
            raise OSError("external connection blocked by test")
        return real_connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (_ for _ in ()).throw(OSError("dns blocked")) if host not in {"127.0.0.1", "localhost"} else [])
    repo = services.indexing.register(str(sample_repo))
    services.indexing.index_now(repo["id"])
    services.analysis.ask(repo["id"], "How is stock added?", use_llm=False, save=False)
    services.analysis.document(repo["id"], use_llm=True, save=False)  # tries (loopback) Ollama; must not go elsewhere
    assert attempts == []


def test_unknown_repository_and_unready(services, tmp_path):
    from services.indexing_service import NotFoundError
    with pytest.raises(NotFoundError):
        services.indexing.status("nope")
    root = tmp_path / "x"; root.mkdir(); (root / "a.py").write_text("x=1\n")
    r = services.indexing.register(str(root))
    with pytest.raises(IndexNotReadyError):
        services.analysis.ask(r["id"], "anything", use_llm=False)


def test_rate_limiter_and_public_settings(settings):
    from backend.auth import RateLimiter
    rl = RateLimiter(3, 60)
    assert [rl.allow("a", now=1), rl.allow("a", now=2), rl.allow("a", now=3), rl.allow("a", now=4)] == [True, True, True, False]
    assert rl.allow("b", now=4) and rl.allow("a", now=100)
    settings.admin_password = "very-secret-pass"
    assert "very-secret-pass" not in str(settings.public_dict())


def test_hosted_settings_force_safe_flags(tmp_path):
    from config.settings import load_settings
    s = load_settings(env={"CODEMIND_HOSTED": "true", "CODEMIND_ALLOW_REPO_WRITES": "true", "CODEMIND_ALLOW_TEST_EXECUTION": "true"},
                      storage_dir=tmp_path / "st")
    assert s.hosted and not s.allow_repo_writes and not s.allow_test_execution
    assert s.clone_workspace == (tmp_path / "st" / "workspace").resolve() and "github.com" in s.allowed_git_hosts


def test_hosted_register_rules(tmp_path):
    from backend.dependencies import build_services
    from config.settings import load_settings
    s = load_settings(env={"CODEMIND_HOSTED": "true"}, storage_dir=tmp_path / "st", embedding_backend="hashing", vector_backend="sqlite")
    svc = build_services(s)
    with pytest.raises(SecurityError, match="disabled"):
        svc.indexing.register(str(tmp_path))
    with pytest.raises(SecurityError, match="not allowed"):
        svc.indexing.register(git_url="https://evil.example.com/a/b")
    with pytest.raises(SecurityError):
        svc.indexing.register(git_url="git@github.com:a/b.git")  # https only when hosted
    svc.db.close()


def test_git_host_allowlist():
    validate_git_url("https://github.com/o/r.git", ["github.com"])
    validate_git_url("git@github.com:o/r.git", ["github.com"])
    for bad in ["https://gitlab.com/o/r", "https://github.com.evil.com/o/r"]:
        with pytest.raises(SecurityError):
            validate_git_url(bad, ["github.com"])


def test_clone_name_is_deterministic():
    from services.indexing_service import IndexingService
    a = IndexingService._clone_name("https://github.com/org/repo.git")
    assert a == IndexingService._clone_name("https://github.com/org/repo.git") and a.startswith("org-repo-")
