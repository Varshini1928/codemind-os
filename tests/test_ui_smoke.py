import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_ui_pure_functions_and_pages(indexed, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")
    svc, rid, repo_dir = indexed
    spec = importlib.util.spec_from_file_location("make_ui_fixtures", ROOT / "scripts" / "make_ui_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps(mod.build(svc, rid, repo_dir), default=str))
    for script in (["web/tests/pure.mjs"], ["web/tests/ui_smoke.mjs", str(fx)], ["web/tests/ui_smoke.mjs", str(fx), "anon"]):
        r = subprocess.run([node, *script], cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
