import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.dependencies import build_services  # noqa: E402
from config.settings import load_settings  # noqa: E402

SAMPLE = ROOT / "sample_repo"


@pytest.fixture
def settings(tmp_path):
    return load_settings(env={}, storage_dir=tmp_path / "storage", embedding_backend="hashing", vector_backend="sqlite",
                         password_iterations=1000)


@pytest.fixture
def services(settings):
    svc = build_services(settings)
    yield svc
    svc.db.close()


@pytest.fixture
def sample_repo(tmp_path):
    dest = tmp_path / "work" / "inventory_sample"
    shutil.copytree(SAMPLE, dest)
    return dest


@pytest.fixture
def indexed(services, sample_repo):
    repo = services.indexing.register(str(sample_repo))
    services.indexing.index_now(repo["id"])
    return services, repo["id"], sample_repo


@pytest.fixture
def tiny_repo(tmp_path):
    """Small synthetic repository created per test."""
    root = tmp_path / "work" / "tiny"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "util.py").write_text("def helper(x):\n    return x + 1\n\n\ndef unused():\n    return 0\n")
    (root / "pkg" / "core.py").write_text("from .util import helper\n\n\nclass Engine:\n    def run(self, v):\n        return self.step(v)\n\n    def step(self, v):\n        return helper(v)\n\n\ndef similar_name():\n    return 1\n")
    (root / "README.md").write_text("# Tiny\n\n## Usage\nCall Engine.run.\n")
    return root
