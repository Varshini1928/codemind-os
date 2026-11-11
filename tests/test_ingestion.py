import os
import pytest

from ingestion.manifest import diff_manifests
from ingestion.repository_loader import RepositoryLoader
from verification.safety_policy import SecurityError


def test_discovery_and_exclusions(tmp_path, settings):
    root = tmp_path / "r"
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "node_modules" / "x" / "i.js").write_text("var a;\n")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (root / "blob.dat2").write_bytes(b"abc\x00def")
    (root / "big.txt").write_text("a" * (settings.max_file_bytes + 10))
    loader = RepositoryLoader(settings)
    m = loader.build_manifest(loader.inspect(str(root)).root)
    assert list(m.files) == ["src/a.py"]
    reasons = m.excluded_counts
    assert reasons["excluded-dir"] >= 2 and reasons["binary-extension"] == 1
    assert reasons["binary-content"] == 1 and reasons["oversized"] == 1


def test_secret_files_and_content_excluded(tmp_path, settings):
    root = tmp_path / "r"
    root.mkdir()
    (root / ".env").write_text("TOKEN=abc\n")
    (root / ".env.example").write_text("TOKEN=\n")
    (root / "id_rsa").write_text("x")
    (root / "k.py").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (root / "ok.py").write_text("print('hi')\n")
    loader = RepositoryLoader(settings)
    m = loader.build_manifest(loader.inspect(str(root)).root)
    assert set(m.files) == {".env.example", "ok.py"}
    assert m.excluded_counts["secret-bearing-filename"] == 2
    assert m.excluded_counts["possible-secret-content"] == 1


def test_symlinks_not_followed(tmp_path, settings):
    root, outside = tmp_path / "r", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (outside / "secret.py").write_text("x=1\n")
    try:
        os.symlink(outside / "secret.py", root / "link.py")
        os.symlink(outside, root / "linkdir")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    (root / "a.py").write_text("y=2\n")
    loader = RepositoryLoader(settings)
    m = loader.build_manifest(loader.inspect(str(root)).root)
    assert list(m.files) == ["a.py"] and m.excluded_counts["symlink"] == 2


def test_invalid_paths_rejected(settings, tmp_path):
    loader = RepositoryLoader(settings)
    for bad in ["", "/", str(tmp_path / "missing"), "/etc"]:
        with pytest.raises(SecurityError):
            loader.inspect(bad)
    f = tmp_path / "file.txt"; f.write_text("x")
    with pytest.raises(SecurityError):
        loader.inspect(str(f))


def test_storage_inside_repo_rejected(tmp_path):
    from config.settings import load_settings
    root = tmp_path / "r"; root.mkdir()
    s = load_settings(env={}, storage_dir=root / ".cm")
    with pytest.raises(SecurityError):
        RepositoryLoader(s).inspect(str(root))


def test_unreadable_and_latin1(tmp_path, settings):
    root = tmp_path / "r"; root.mkdir()
    (root / "l.py").write_bytes("# caf\xe9\nx = 1\n".encode("latin-1"))
    loader = RepositoryLoader(settings)
    m = loader.build_manifest(root)
    assert m.files["l.py"]["encoding"] == "latin-1-fallback"


def test_manifest_diff(tmp_path, settings):
    root = tmp_path / "r"; root.mkdir()
    (root / "a.py").write_text("1\n"); (root / "b.py").write_text("2\n")
    loader = RepositoryLoader(settings)
    old = loader.build_manifest(root)
    (root / "a.py").write_text("changed\n"); (root / "b.py").unlink(); (root / "c.py").write_text("3\n")
    d = diff_manifests(old, loader.build_manifest(root))
    assert (d.added, d.modified, d.deleted) == (["c.py"], ["a.py"], ["b.py"])


def test_indexing_is_read_only(indexed):
    svc, rid, root = indexed
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    svc.indexing.index_now(rid, full=True)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_empty_repository(services, tmp_path):
    root = tmp_path / "empty"; root.mkdir()
    r = services.indexing.register(str(root))
    res = services.indexing.index_now(r["id"])
    assert any("No indexable files" in w for w in res["warnings"])
    assert services.indexing.status(r["id"])["status"] == "ready"
