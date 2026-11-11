"""Bounded graph traversal and change-impact analysis. Only extracted edges are followed."""
from __future__ import annotations

from collections import deque
from typing import Iterable

import networkx as nx

INTENT_EDGES: dict[str, set[str]] = {
    "architecture": {"CONTAINS", "IMPORTS", "EXPOSES", "DEPLOYS", "READS_CONFIG"},
    "question": {"CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS"},
    "impact": {"CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "READS_CONFIG", "TESTS", "EXPOSES", "WRITES_ENTITY"},
    "tests": {"TESTS", "CALLS", "IMPORTS"},
    "documentation": {"CONTAINS", "IMPORTS", "EXPOSES", "READS_CONFIG", "INHERITS"},
    "quality": {"CALLS", "IMPORTS", "READS_CONFIG"},
}
STRUCTURAL = {"CONTAINS"}


def neighbors(g: nx.MultiDiGraph, starts: Iterable[str], direction: str = "both", edge_types: set[str] | None = None,
              max_depth: int = 2, max_nodes: int = 400) -> list[dict]:
    """Breadth-first traversal. direction: out (dependencies), in (dependents), both."""
    seen = {s: 0 for s in starts if s in g}
    out: list[dict] = []
    q = deque(seen)
    while q and len(out) < max_nodes:
        cur = q.popleft()
        depth = seen[cur]
        if depth >= max_depth:
            continue
        steps = []
        if direction in {"out", "both"}:
            steps += [(v, d, "out") for _, v, d in g.out_edges(cur, data=True)]
        if direction in {"in", "both"}:
            steps += [(u, d, "in") for u, _, d in g.in_edges(cur, data=True)]
        for other, d, way in steps:
            if edge_types and d["type"] not in edge_types:
                continue
            if other in seen:
                continue
            seen[other] = depth + 1
            q.append(other)
            out.append({"node": other, "depth": depth + 1, "via": d["type"], "from": cur, "direction": way,
                        "resolution": d.get("resolution", "resolved"), "method": d.get("method", ""),
                        "line": d.get("line", 0), "path": d.get("path", "")})
    return out


def subgraph_view(g: nx.MultiDiGraph, center: str, depth: int = 1, direction: str = "both",
                  edge_types: set[str] | None = None, max_nodes: int = 150) -> dict:
    reached = neighbors(g, [center], direction, edge_types, depth, max_nodes)
    ids = [center] + [r["node"] for r in reached]
    idset = set(ids)
    nodes = [{"id": n, **{k: v for k, v in g.nodes[n].items() if k != "attrs"}} for n in ids if n in g]
    edges = [{"source": u, "target": v, **d} for u, v, d in g.edges(data=True)
             if u in idset and v in idset and (not edge_types or d["type"] in edge_types)]
    return {"center": center, "nodes": nodes, "edges": edges, "truncated": len(reached) >= max_nodes}


def resolve_target(g: nx.MultiDiGraph, target: str) -> dict:
    """Map a user-supplied file path or symbol name to graph nodes. Reports ambiguity instead of guessing."""
    t = target.strip()
    if g.has_node(t):
        return {"kind": "node", "ids": [t], "candidates": []}
    file_id = f"file:{t}"
    if file_id in g:
        ids = [n for _, n, d in g.out_edges(file_id, data=True) if d["type"] == "CONTAINS"]
        mod = ids[0] if ids else None
        inner = [mod] + [n for _, n, d in g.out_edges(mod, data=True) if d["type"] == "CONTAINS"] if mod else []
        return {"kind": "file", "ids": [file_id] + inner, "candidates": [], "file": t}
    exact = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == t and d.get("type") not in {"Endpoint"}]
    if len(exact) == 1:
        return {"kind": "symbol", "ids": exact, "candidates": []}
    suffix = [n for n, d in g.nodes(data=True) if d.get("qualified_name", "").endswith("." + t)
              and d.get("type") in {"Function", "Method", "Class", "DatabaseEntity", "Test"}]
    suffix = exact or suffix
    if len(suffix) == 1:
        return {"kind": "symbol", "ids": suffix, "candidates": []}
    if suffix:
        return {"kind": "ambiguous", "ids": [], "candidates": [
            {"id": n, "qualified_name": g.nodes[n]["qualified_name"], "path": g.nodes[n].get("path")} for n in suffix[:20]]}
    return {"kind": "unknown", "ids": [], "candidates": []}


def _item(g, n, depth, via, resolution, frm, line=0, path=""):
    d = g.nodes[n]
    return {"id": n, "type": d.get("type"), "name": d.get("name"), "qualified_name": d.get("qualified_name", ""),
            "path": d.get("path", ""), "start_line": d.get("start_line"), "end_line": d.get("end_line"),
            "depth": depth, "via": via, "resolution": resolution, "from": frm, "edge_line": line, "edge_path": path}


def impact_analysis(g: nx.MultiDiGraph, target_ids: list[str], max_depth: int = 3) -> dict:
    """Who depends on the target (reverse traversal) and what the target depends on, with labels."""
    targets = set(target_ids)
    # Transitive hops follow only call/inheritance/exposure edges. Importing a module does not imply using the changed
    # symbol, so IMPORTS edges are reported at depth 1 only (the importer is directly affected, its importers are not).
    rev = neighbors(g, targets, "in", {"CALLS", "INHERITS", "IMPLEMENTS", "EXPOSES"}, max_depth)
    seen_ids = {r["node"] for r in rev}
    rev += [r for r in neighbors(g, targets, "in", {"IMPORTS"}, 1) if r["node"] not in seen_ids]
    direct, transitive, heuristic_only = [], [], []
    for r in rev:
        n = r["node"]
        if n in targets or g.nodes[n].get("type") in {"File", "Directory", "Repository"}:
            continue
        it = _item(g, n, r["depth"], r["via"], r["resolution"], r["from"], r["line"], r["path"])
        (direct if r["depth"] == 1 else transitive).append(it)
        if r["resolution"] != "resolved":
            heuristic_only.append(it)
    affected = targets | {i["id"] for i in direct + transitive}
    core = targets | {i["id"] for i in direct + transitive if i["via"] != "IMPORTS"}

    implementations = [_item(g, r["node"], r["depth"], r["via"], r["resolution"], r["from"], r["line"], r["path"])
                       for r in neighbors(g, targets, "in", {"INHERITS", "IMPLEMENTS"}, max_depth)]

    tests: dict[str, dict] = {}
    for a in affected:
        for u, _, d in g.in_edges(a, data=True):
            if d["type"] == "TESTS" and u not in tests:
                tests[u] = _item(g, u, 1, "TESTS", d["resolution"], a, d.get("line", 0), d.get("path", ""))
    for a in core:  # test files that import the module containing an affected symbol
        mods = {a} if g.nodes[a].get("type") == "Module" else set()
        p = g.nodes[a].get("path")
        if p and f"file:{p}" in g:
            mods |= {n for _, n, d in g.out_edges(f"file:{p}", data=True) if g.nodes[n].get("type") == "Module"}
        for m in mods:
            for u, _, d in g.in_edges(m, data=True):
                if d["type"] == "IMPORTS" and g.nodes[u].get("type") == "Module" and u not in tests and _is_test_path(g.nodes[u].get("path", "")):
                    tests[u] = _item(g, u, 1, "IMPORTS", "heuristic", m, d.get("line", 0), d.get("path", ""))
                    tests[u]["note"] = "test module imports the affected module; whether it exercises the change is unknown"

    configs, endpoints = {}, {}
    for a in affected:
        for _, v, d in g.out_edges(a, data=True):
            if d["type"] == "READS_CONFIG":
                configs.setdefault(v, _item(g, v, 1, "READS_CONFIG", d["resolution"], a, d.get("line", 0), d.get("path", "")))
            if d["type"] == "EXPOSES":
                endpoints.setdefault(v, _item(g, v, 1, "EXPOSES", d["resolution"], a, d.get("line", 0), d.get("path", "")))
    names = {g.nodes[t].get("name") for t in targets if g.nodes[t].get("type") not in {"File", "Module"}}
    unresolved = [u for u in g.graph.get("unresolved_calls", [])
                  if u["name"].split(".")[-1] in names and u["src"] not in targets]
    deps = [_item(g, r["node"], r["depth"], r["via"], r["resolution"], r["from"], r["line"], r["path"])
            for r in neighbors(g, targets, "out", {"CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "WRITES_ENTITY"}, 1)
            if g.nodes[r["node"]].get("type") not in {"File", "Directory"}]
    return {
        "targets": [_item(g, t, 0, "", "resolved", "") for t in target_ids if t in g],
        "dependencies": deps,
        "direct_dependents": direct,
        "transitive_dependents": transitive,
        "implementations": implementations,
        "tests": list(tests.values()),
        "configuration": list(configs.values()),
        "exposed_endpoints": list(endpoints.values()),
        "unresolved_possible_callers": [
            {"path": u["path"], "line": u["line"], "call": u["name"], "caller": g.nodes[u["src"]].get("qualified_name", u["src"])}
            for u in unresolved[:100]],
        "limits": ["Dynamic dispatch, reflection, runtime-loaded plugins, remote APIs and cross-language calls "
                   "are not visible to static analysis; coverage of dependents is therefore not complete."],
        "max_depth": max_depth,
    }


def _is_test_path(path: str) -> bool:
    from pathlib import PurePosixPath
    p = PurePosixPath(path)
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts
