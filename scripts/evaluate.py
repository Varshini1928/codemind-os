"""Reproducible evaluation of CodeMind OS on a repository with manually verified expectations.

  python scripts/evaluate.py --repo sample_repo --dataset evaluation/dataset_sample.json

Measured: Recall@k, MRR, citation precision/coverage (deterministic path), impact precision/recall, indexing and query
latency, peak memory, index size and unexpected external network connections. Anything that was not collected is
reported as "not evaluated" - nothing is estimated. Results: evaluation/results/latest.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import resource
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.dependencies import build_services  # noqa: E402
from config.settings import load_settings  # noqa: E402
from evaluation.metrics import mean, percentile, precision_recall, recall_at_k, reciprocal_rank  # noqa: E402

CONFIGS = {"lexical-only": "lexical", "semantic-only": "semantic", "semantic+graph": "semantic_graph", "full-hybrid": "hybrid"}
NE = "not evaluated"


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(ROOT / "sample_repo"))
    ap.add_argument("--dataset", default=str(ROOT / "evaluation" / "dataset_sample.json"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--embedding", default="auto", help="auto | hashing | sentence-transformers")
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "results"))
    a = ap.parse_args()
    data = json.loads(Path(a.dataset).read_text(encoding="utf-8"))

    external: list[str] = []
    real_connect = socket.socket.connect

    def guard(self, address, *args, **kw):
        host = address[0] if isinstance(address, tuple) else str(address)
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in {"127.0.0.1", "::1", "localhost"}:
            external.append(str(host))
        return real_connect(self, address, *args, **kw)

    socket.socket.connect = guard  # type: ignore[assignment]
    tmp = Path(tempfile.mkdtemp(prefix="codemind_eval_"))
    s = load_settings(env={}, storage_dir=tmp / "storage", embedding_backend=a.embedding, vector_backend="auto")
    svc = build_services(s)
    repo = svc.indexing.register(a.repo)
    t0 = time.perf_counter()
    idx = svc.indexing.index_now(repo["id"], full=True)
    index_s = time.perf_counter() - t0
    st = svc.indexing.status(repo["id"])
    embedder = st["embedder"]
    results: dict = {"environment": {"python": sys.version.split()[0], "embedder": embedder, "vector_store": svc.vectors.name,
                                     "semantic_embeddings": not embedder.startswith("hashing"),
                                     "repository_revision": st["revision"], "files": st["file_count"], "chunks": st["chunk_count"],
                                     "queries": len(data["queries"])},
                     "retrieval": {}, "citations": {}, "impact": {}, "performance": {}}
    for name, mode in CONFIGS.items():
        r1, r3, rk, rr, lat = [], [], [], [], []
        for q in data["queries"]:
            t = time.perf_counter()
            b = svc.searcher.search(repo["id"], q["question"], q.get("intent", "question"), mode, a.k)
            lat.append((time.perf_counter() - t) * 1000)
            ranked = [{"path": i.path, "symbol": i.symbol, "start_line": i.start_line, "end_line": i.end_line} for i in b.items]
            r1.append(recall_at_k(ranked, q["expected"], 1)); r3.append(recall_at_k(ranked, q["expected"], 3))
            rk.append(recall_at_k(ranked, q["expected"], a.k)); rr.append(reciprocal_rank(ranked, q["expected"]))
        results["retrieval"][name] = {"recall@1": mean(r1), "recall@3": mean(r3), f"recall@{a.k}": mean(rk), "mrr": mean(rr),
                                      "query_latency_ms_mean": mean(lat), "query_latency_ms_p95": percentile(lat, 95)}
    # citation precision / coverage of the deterministic (no-LLM) analysis path
    tot = cov = valid = 0
    for q in data["queries"]:
        res = svc.analysis.ask(repo["id"], q["question"], use_llm=False, save=False)
        v = res["verification"]
        tot += v["total"]; cov += v["claims_with_evidence"]; valid += v["citation_valid"]
    results["citations"] = {"path": "deterministic analysis without an LLM", "claims": tot,
                            "citation_coverage": (cov / tot) if tot else NE,
                            "citation_precision": (valid / cov) if cov else NE,
                            "note": "Citation validity != semantic correctness. LLM-path citation quality: " + NE + " (needs a running Ollama model; not part of this script yet)."}
    pr, rc, prs, rcs = [], [], [], []
    for it in data.get("impact", []):
        res = svc.analysis.impact(repo["id"], it["target"], use_llm=False, save=False)
        p = res["sections"][0]["payload"]
        own = {x["path"] for x in p["target"]["nodes"] if x.get("path")}
        items = [x for sec in ("direct_dependents", "transitive_dependents", "tests") for x in p.get(sec, []) if x["path"]]
        pred = {x["path"] for x in items} - own
        strict = {x["path"] for x in items if x["resolution"] == "resolved"} - own
        pp, rr_ = precision_recall(pred, set(it["expected_files"]))
        ps, rs = precision_recall(strict, set(it["expected_files"]))
        pr.append(pp); rc.append(rr_); prs.append(ps); rcs.append(rs)
        results["impact"][it["target"]] = {"predicted": sorted(pred), "expected": it["expected_files"], "precision": pp, "recall": rr_,
                                           "precision_resolved_only": ps, "recall_resolved_only": rs}
    results["impact"]["_mean"] = {"precision": mean(pr), "recall": mean(rc), "precision_resolved_only": mean(prs),
                                  "recall_resolved_only": mean(rcs)}
    ratings = data.get("ratings") or []
    results["usefulness"] = {"human_rated_usefulness": mean([r["score"] for r in ratings]) if ratings else NE}
    results["performance"] = {"indexing_latency_s": round(index_s, 3), "files_indexed": idx["added"],
                              "peak_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                              "index_size_kb": round(dir_size(s.storage_dir) / 1024, 1),
                              "unexpected_external_connections": len(external), "external_hosts": sorted(set(external))}
    socket.socket.connect = real_connect  # type: ignore[assignment]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else str(v)  # noqa: E731
    md = ["# Evaluation results (measured)", "", f"Environment: `{json.dumps(results['environment'])}`", "",
          "| Configuration | " + " | ".join(next(iter(results['retrieval'].values())).keys()) + " |",
          "|---|" + "---|" * len(next(iter(results['retrieval'].values())))]
    for n, m in results["retrieval"].items():
        md.append(f"| {n} | " + " | ".join(fmt(v) for v in m.values()) + " |")
    md += ["", f"Citations: `{json.dumps(results['citations'])}`", "", f"Impact (mean): `{json.dumps(results['impact']['_mean'])}`", "",
           f"Usefulness: `{json.dumps(results['usefulness'])}`", f"Performance: `{json.dumps(results['performance'])}`"]
    (out / "latest.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    svc.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
