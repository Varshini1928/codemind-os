"""CodeMind OS entry point.

  python main.py serve                      start the web UI + API on http://127.0.0.1:8000
  python main.py index PATH [--full]        register + index a repository (read-only)
  python main.py ask REPO_ID "question"     ask a question (add --no-llm for deterministic mode)
  python main.py review|impact|tests|docs   run the specialist analyses
  python main.py report REPO_ID KIND        print the latest saved report as Markdown
  python main.py health                     check Ollama, storage and dependencies
"""
from __future__ import annotations

import argparse
import json
import sys


def _svc():
    from backend.dependencies import get_services
    return get_services()


def _print_result(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, indent=2, default=str))
        return
    from services.report_service import render_markdown
    print(render_markdown(res))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codemind", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve"); sv.add_argument("--host"); sv.add_argument("--port", type=int)
    ix = sub.add_parser("index"); ix.add_argument("path"); ix.add_argument("--full", action="store_true")
    for name in ("ask", "review", "impact", "tests", "docs"):
        p = sub.add_parser(name)
        p.add_argument("repo_id")
        p.add_argument("text", nargs="?", default="", help="question / focus / target / doc kind")
        p.add_argument("--no-llm", action="store_true"); p.add_argument("--json", action="store_true")
        p.add_argument("--no-save", action="store_true")
    rp = sub.add_parser("report"); rp.add_argument("repo_id"); rp.add_argument("kind", nargs="?")
    sub.add_parser("health")
    sub.add_parser("repos")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        from config.settings import load_settings
        s = load_settings()
        host, port = a.host or s.api_host, a.port or s.api_port
        public_ok = s.auth_mode == "accounts" and (s.hosted or s.allow_network_bind)
        if host not in {"127.0.0.1", "localhost", "::1"} and not public_ok:
            print("Refusing to bind to a non-loopback address. Expose it only with user accounts enabled "
                  "(CODEMIND_AUTH_MODE=accounts) and either CODEMIND_HOSTED=true or CODEMIND_ALLOW_NETWORK_BIND=true.", file=sys.stderr)
            return 2
        print(f"CodeMind OS web UI + API: http://{host}:{port}/", file=sys.stderr)
        from backend.api import create_app
        uvicorn.run(create_app(), host=host, port=port, log_level="info")
        return 0
    svc = _svc()
    if a.cmd == "repos":
        for r in svc.indexing.list_repos():
            print(f"{r['id']}\t{r['status']}\t{r['root']}")
        return 0
    if a.cmd == "health":
        print(json.dumps({"llm": svc.analysis.llm_status(True), "vector_store": svc.vectors.name,
                          "storage": str(svc.settings.storage_dir), "database_ok": svc.db.integrity_ok()}, indent=2))
        return 0
    if a.cmd == "index":
        r = svc.indexing.register(a.path)
        res = svc.indexing.index_now(r["id"], a.full, lambda f, m: print(f"[{f:4.0%}] {m}", file=sys.stderr))
        print(json.dumps({"repo_id": r["id"], **res}, indent=2))
        return 0
    if a.cmd == "report":
        items = [x for x in svc.reports.list(a.repo_id) if not a.kind or x["kind"] == a.kind]
        if not items:
            print("No reports found.", file=sys.stderr)
            return 1
        print(svc.reports.read(a.repo_id, items[0]["report_id"]))
        return 0
    kw = {"use_llm": not a.no_llm, "save": not a.no_save}
    an = svc.analysis
    res = {"ask": lambda: an.ask(a.repo_id, a.text or "Describe this repository", **kw),
           "review": lambda: an.quality_review(a.repo_id, a.text, **kw),
           "impact": lambda: an.impact(a.repo_id, a.text, **kw),
           "tests": lambda: an.test_plan(a.repo_id, a.text, **kw),
           "docs": lambda: an.document(a.repo_id, a.text or "full", **kw)}[a.cmd]()
    _print_result(res, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
