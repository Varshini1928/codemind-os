"""Repository analyst: architecture overviews and evidence-grounded question answering."""
from __future__ import annotations

from collections import Counter

from agents.common import CLAIM_SCHEMA, AgentContext, AgentOutput, claims_from_llm
from verification.evidence_validator import Claim


def _architecture_facts(ctx: AgentContext, out: AgentOutput) -> None:
    g = ctx.graph()
    # entry points
    for n, d in g.nodes(data=True):
        if d.get("type") == "Module" and d.get("attrs", {}).get("entry_point"):
            ml = d["attrs"].get("main_line", [1, 1])
            ev = ctx.span(d["path"], ml[0], ml[1], d["qualified_name"])
            out.claims.append(Claim(f"`{d['path']}` defines a `__main__` guard, so it can be executed as an entry point.",
                                    "fact", [ev] if ev else []))
    # endpoints
    eps = [(d["path"], d["start_line"], d["end_line"], d["name"], d.get("attrs", {}).get("handler", ""))
           for _, d in g.nodes(data=True) if d.get("type") == "Endpoint"]
    for path, a, b, name, handler in sorted(eps)[:15]:
        ev = ctx.span(path, a, b, handler)
        out.claims.append(Claim(f"Endpoint `{name}` is declared in `{path}` and handled by `{handler}` "
                                "(detected from a decorator pattern; framework not verified).", "fact", [ev] if ev else []))
    # most imported internal modules
    indeg: Counter = Counter()
    sites: dict[str, list] = {}
    for u, v, d in g.edges(data=True):
        if d["type"] == "IMPORTS" and d["resolution"] == "resolved" and g.nodes[v].get("type") == "Module" \
                and g.nodes[u].get("path") != g.nodes[v].get("path"):
            indeg[v] += 1
            sites.setdefault(v, []).append((d["path"], d["line"]))
    for v, n in indeg.most_common(5):
        evs = [e for e in (ctx.span(p, ln, ln) for p, ln in sorted(set(sites[v]))[:3]) if e]
        out.claims.append(Claim(f"Module `{g.nodes[v]['qualified_name']}` is imported by {n} other module(s) "
                                "(resolved static imports), making it a central dependency.", "fact", evs))
    # configuration
    for n, d in sorted(g.nodes(data=True), key=lambda kv: kv[0]):
        if d.get("type") == "ConfigKey":
            for u, _, e in g.in_edges(n, data=True):
                if e["type"] == "READS_CONFIG":
                    ev = ctx.span(e["path"], e["line"], e["line"])
                    out.claims.append(Claim(f"Environment variable `{d['name']}` is read in "
                                            f"`{g.nodes[u].get('qualified_name')}`.", "fact", [ev] if ev else []))
                    break
    # deployment
    for u, v, e in g.edges(data=True):
        if e["type"] == "DEPLOYS":
            ev = ctx.span(e["path"], e["line"], e["line"])
            out.claims.append(Claim(f"`{e['path']}` references `{g.nodes[v].get('path')}` (text match - heuristic, "
                                    "not a verified deployment relationship).", "fact", [ev] if ev else []))
    ext = g.graph.get("external_imports", {})
    if ext:
        top = sorted(ext.items(), key=lambda kv: -kv[1])[:8]
        evs = []
        for name, _ in top[:4]:
            for u, v, e in g.in_edges(f"ext:{name}", data=True):
                ev = ctx.span(e["path"], e["line"], e["line"])
                if ev:
                    evs.append(ev)
                break
        out.claims.append(Claim("External modules imported (by number of import statements): "
                                + ", ".join(f"`{n}` ({c})" for n, c in top) + ". Evidence shows a sample of the imports.",
                                "fact", evs))
    stats = {"files": sum(1 for _, d in g.nodes(data=True) if d.get("type") == "File"),
             "entities": Counter(d["type"] for _, d in g.nodes(data=True)
                                 if d.get("type") not in {"Repository", "Directory", "File"} and not d.get("external")),
             "unresolved_calls": len(g.graph.get("unresolved_calls", []))}
    out.payload["statistics"] = {"files": stats["files"], "entities": dict(stats["entities"]),
                                 "unresolved_call_sites": stats["unresolved_calls"],
                                 "note": "Computed from the extracted graph; unresolved call sites are not edges."}


def run(ctx: AgentContext, question: str, task: str = "question", options: dict | None = None) -> AgentOutput:
    out = AgentOutput()
    intent = "architecture" if task == "architecture" else "question"
    bundle = ctx.search(question, intent)
    out.notes += bundle.uncertainty
    out.payload["retrieval"] = {"mode": bundle.mode, "stats": bundle.stats, "evidence_ids": [i.id for i in bundle.items]}
    if task == "architecture":
        _architecture_facts(ctx, out)
    if not bundle.sufficient and task != "architecture":
        out.insufficient = True
        out.summary = ("Insufficient evidence: no indexed code or documentation matched this question strongly enough "
                       "to answer without guessing. Try naming a file, function or class, or re-index the repository.")
        out.payload["weak_matches"] = [i.to_dict() for i in bundle.items[:5]]
        return out
    if task != "architecture":
        for it in bundle.items[:6]:
            what = f"`{it.symbol}`" if it.symbol and not it.symbol.startswith("<") else "code"
            out.claims.append(Claim(f"Relevant {what} is located in `{it.path}` lines {it.start_line}-{it.end_line} "
                                    f"(retrieved via {', '.join(it.methods)}).", "fact", [it.id]))
    data = ctx.llm_json(
        "Answer the user's question about the repository." if task != "architecture" else "Describe the repository architecture.",
        "Explain using only the evidence. Keep claims short and specific.", CLAIM_SCHEMA, question, bundle.items)
    if data is not None:
        out.mode = "llm+deterministic"
        out.summary = str(data.get("summary", ""))[:1500]
        out.claims += claims_from_llm(data, ctx)
        if data.get("insufficient_evidence"):
            out.notes.append("The model reported insufficient evidence: " + "; ".join(map(str, data.get("unknowns", []))[:3]) if False
                             else "The model reported insufficient evidence for parts of the question.")
        for u in data.get("unknowns", []) or []:
            out.notes.append(f"Unknown: {u}")
    else:
        out.notes.append("AI interpretation skipped: " + (ctx.llm_error or "no local model configured")
                         + ". Showing deterministic, evidence-linked results only.")
        out.summary = (f"{len(out.claims)} evidence-linked finding(s) from static analysis and retrieval "
                       "(no AI interpretation).")
    return out
