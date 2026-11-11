"""Builds JSON fixtures from REAL backend output (used by the Node UI smoke test).

  python scripts/make_ui_fixtures.py out.json            # indexes sample_repo in a temporary storage dir
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(svc, rid: str, repo_dir: Path) -> dict:
    from graph.dependency_analyzer import resolve_target, subgraph_view
    A = svc.analysis
    kw = dict(use_llm=False, save=True)
    fx = {"repo": svc.indexing.status(rid), "overview": svc.indexing.overview(rid), "files": svc.indexing.list_files(rid),
          "symbols": svc.indexing.find_symbols(rid, "add_stock"),
          "ask": A.ask(rid, "What is the architecture and what are the security risks?", **kw),
          "ask_insufficient": A.ask(rid, "How does the zebra blockchain replicate?", **kw),
          "quality": A.quality_review(rid, **kw), "impact": A.impact(rid, "inventory.service.add_stock", **kw),
          "impact_bad": A.impact(rid, "nothing_here", **kw), "tests": A.test_plan(rid, "apply_discount", **kw),
          "docs": A.document(rid, "full", **kw), "reports": svc.reports.list(rid)}
    g = svc.graphs.load(rid)
    fx["graph"] = subgraph_view(g, resolve_target(g, "add_stock")["ids"][0], 2, "both", None)
    lines = (repo_dir / "inventory" / "service.py").read_text().split("\n")[:6]
    fx["source"] = {"path": "inventory/service.py", "revision": fx["repo"]["revision"], "start_line": 1, "end_line": 6, "total_lines": 40, "lines": lines}
    fx["report_md"] = svc.reports.read(rid, fx["reports"][0]["report_id"], "markdown")
    fx["settings"] = svc.settings.public_dict()
    from backend import views
    admin = svc.auth.get(svc.indexing.get_repo(rid)["owner_id"]) if svc.indexing.get_repo(rid).get("owner_id") else svc.auth.seed_admin("admin@example.com", "correct horse 42")
    svc.settings.signup_mode = "approval"
    pending = svc.auth.signup("pending.user@example.com", "another phrase 77", "Pending User")
    fx["me"] = views.me_payload(svc, admin)
    fx["admin_users"], fx["admin_stats"] = views.admin_users(svc), views.admin_stats(svc)
    fx["admin_repos"], fx["audit"] = views.admin_repos(svc), svc.auth.audit_list(50)
    fx["admin_user"], fx["pending_id"] = admin.public(), pending.id
    fx["ready"] = {"ready": True, "database_ok": True, "vector_store": "sqlite-numpy", "git_available": True, "hosted": False,
                   "llm": {"available": False, "model": "qwen2.5-coder:7b", "model_installed": False, "message": "Ollama not reachable"}}
    return fx


def main() -> int:
    from backend.dependencies import build_services
    from config.settings import load_settings
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ui_fixtures.json")
    s = load_settings(env={}, storage_dir=Path(tempfile.mkdtemp()), embedding_backend="hashing", vector_backend="sqlite", password_iterations=1000)
    svc = build_services(s)
    admin = svc.auth.seed_admin("admin@example.com", "correct horse 42")
    repo = svc.indexing.register(str(ROOT / "sample_repo"), owner_id=admin.id)
    svc.indexing.index_now(repo["id"])
    out.write_text(json.dumps(build(svc, repo["id"], ROOT / "sample_repo"), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
