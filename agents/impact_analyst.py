"""Impact analyst: bounded reverse/forward graph traversal; labels resolved vs heuristic vs unresolved."""
from __future__ import annotations

from agents.common import AgentContext, AgentOutput, claims_from_llm
from graph.dependency_analyzer import impact_analysis, resolve_target
from verification.evidence_validator import Claim

SECTIONS = ["direct_dependents", "transitive_dependents", "implementations", "tests", "configuration",
            "exposed_endpoints", "dependencies"]


def run(ctx: AgentContext, question: str, task: str = "impact", options: dict | None = None) -> AgentOutput:
    options = options or {}
    out = AgentOutput()
    target = (options.get("target") or "").strip()
    if not target:
        out.insufficient = True
        out.summary = "Insufficient input: select a target file path or symbol name for change-impact analysis."
        return out
    g = ctx.graph()
    res = resolve_target(g, target)
    if res["kind"] in {"unknown", "ambiguous"}:
        out.insufficient = True
        out.payload["candidates"] = res["candidates"]
        out.summary = (f"Target '{target}' is ambiguous; choose one of the candidates." if res["candidates"]
                       else f"Target '{target}' was not found in the indexed graph.")
        return out
    depth = min(int(options.get("depth") or ctx.settings.max_graph_depth + 1), 5)
    imp = impact_analysis(g, res["ids"], depth)
    out.payload["target"] = {"input": target, "resolved_as": res["kind"], "nodes": imp["targets"][:30]}
    for sec in SECTIONS:
        for item in imp[sec]:
            a, b = (item["edge_path"], item["edge_line"]) if item.get("edge_path") and item.get("edge_line") else (item["path"], item["start_line"])
            ev = ctx.span(a, int(b or 1), int(b or 1), item.get("qualified_name", ""), ("graph",)) if a else None
            item["evidence_ids"] = [ev] if ev else []
            item["label"] = ("statically resolved" if item["resolution"] == "resolved" else
                             "heuristic" if item["resolution"] == "heuristic" else item["resolution"])
    tnames = ", ".join(f"`{t['qualified_name'] or t['path']}`" for t in imp["targets"][:3]) or f"`{target}`"
    for it in imp["direct_dependents"][:25]:
        out.claims.append(Claim(f"`{it['qualified_name']}` directly depends on {tnames} via {it['via']} "
                                f"({it['label']}) at `{it['edge_path']}` line {it['edge_line']}.", "fact", it["evidence_ids"]))
    for it in imp["transitive_dependents"][:25]:
        out.claims.append(Claim(f"`{it['qualified_name']}` may be affected transitively ({it['depth']} hops, last link {it['via']}, "
                                f"{it['label']}).", "interpretation", it["evidence_ids"]))
    for it in imp["tests"][:20]:
        out.claims.append(Claim(f"Test `{it['qualified_name']}` is related to the change ({it['label']}); "
                                + it.get("note", "linked by a static call from the test."), "fact", it["evidence_ids"]))
    for it in imp["configuration"][:15]:
        out.claims.append(Claim(f"Configuration key `{it['name']}` is read by an affected symbol ({it['label']}).", "fact", it["evidence_ids"]))
    for it in imp["exposed_endpoints"][:10]:
        out.claims.append(Claim(f"Affected symbol exposes endpoint `{it['name']}` ({it['label']}): externally visible behaviour may change.",
                                "interpretation", it["evidence_ids"]))
    # documentation that mentions the target
    names = {t["name"] for t in imp["targets"] if t.get("type") not in {"File"} and t.get("name")} or {target}
    docs = []
    for nm in list(names)[:3]:
        try:
            b = ctx.search(nm, "documentation", "lexical", 4, {"doc"})
        except Exception:
            continue
        for i in b.items:
            if i.scores["raw"].get("bm25", 0) >= ctx.settings.min_bm25 or i.scores["raw"].get("exact", 0):
                docs.append({"path": i.path, "lines": f"{i.start_line}-{i.end_line}", "evidence_ids": [i.id]})
    out.payload["documentation_to_review"] = docs[:10]
    for d in docs[:10]:
        out.claims.append(Claim(f"Documentation `{d['path']}` lines {d['lines']} mentions the target and may need an update.",
                                "interpretation", d["evidence_ids"]))
    out.payload.update({k: imp[k] for k in SECTIONS + ["unresolved_possible_callers", "limits", "max_depth"]})
    test_mods = sorted({t["path"] for t in imp["tests"] if t["path"]})
    out.payload["recommended_tests"] = {"to_run": test_mods,
                                        "note": "Suggestions only - tests are never executed automatically."}
    out.payload["labels"] = {"statically resolved": "edge found by static name resolution",
                             "heuristic": "plausible but unproven (decorator/naming/text match)",
                             "unresolved": "possible callers that static analysis could not link"}
    if imp["unresolved_possible_callers"]:
        out.notes.append(f"{len(imp['unresolved_possible_callers'])} unresolved call site(s) use the same name; "
                         "they may be callers (dynamic dispatch / untyped receivers) but are NOT confirmed.")
    out.notes += imp["limits"]
    ev_items = [ctx.registry.get(i) for sec in ("direct_dependents", "tests") for it in imp[sec][:6] for i in it["evidence_ids"]]
    data = ctx.llm_json("Summarise the risk of the proposed change for a reviewer.",
                        "Use only the evidence. Do not claim completeness; mention dynamic-dispatch limits.",
                        '{"summary": str, "claims": [{"text": str, "kind": "interpretation|recommendation", "evidence": ["E1"]}]}',
                        (options.get("change") or question) + f" (target: {target})", [e for e in ev_items if e])
    if data is not None:
        out.summary = str(data.get("summary", ""))[:1200]
        out.claims += claims_from_llm(data, ctx)
        out.mode = "llm+deterministic"
    else:
        out.notes.append("AI risk summary skipped: " + (ctx.llm_error or "no local model configured"))
    if not out.summary:
        out.summary = (f"{len(imp['direct_dependents'])} direct and {len(imp['transitive_dependents'])} transitive dependent(s), "
                       f"{len(imp['tests'])} related test(s), {len(imp['configuration'])} configuration key(s) "
                       f"(depth {depth}). Static analysis only: dynamic dependencies are not covered.")
    return out
