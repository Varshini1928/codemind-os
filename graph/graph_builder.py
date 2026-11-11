"""Builds the repository knowledge graph (NetworkX MultiDiGraph) from parse results.

Every edge records: type, extraction method, evidence location, and resolution status:
  resolved   - target found by static name resolution (imports / scopes / inheritance)
  heuristic  - plausible but not proven (decorator patterns, text matches, naming conventions)
  external   - target lies outside the repository (third-party or stdlib module)
Unresolved call targets are never turned into edges; they are listed in graph.graph['unresolved_calls'].
LLM output is never used here, so no edge is an AI inference.
"""
from __future__ import annotations

import posixpath
from collections import defaultdict
from pathlib import PurePosixPath

import networkx as nx

from parsing.entity_extractor import Entity, ParseResult
from parsing.languages import detect_language

MAX_UNRESOLVED = 5000
_BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__)


def _add_edge(g: nx.MultiDiGraph, u: str, v: str, etype: str, method: str, path: str = "", line: int = 0,
              resolution: str = "resolved", **extra) -> None:
    if u == v and etype != "CALLS":
        return
    if g.has_edge(u, v):
        for _, d in g[u][v].items():
            if d["type"] == etype:
                d["count"] = d.get("count", 1) + 1
                return
    g.add_edge(u, v, type=etype, method=method, path=path, line=line, resolution=resolution,
               confidence=1.0 if resolution == "resolved" else (0.5 if resolution == "heuristic" else 0.2), count=1,
               **extra)


def _node_attrs(e: Entity) -> dict:
    return {"type": e.type, "name": e.name, "qualified_name": e.qualified_name, "path": e.path,
            "start_line": e.start_line, "end_line": e.end_line, "language": e.language,
            "content_hash": e.content_hash, "extraction_method": e.extraction_method,
            "confidence": e.confidence, **({"attrs": {k: v for k, v in e.attrs.items()
                                                      if k in {"docstring", "signature", "route", "http_method",
                                                               "handler", "entry_point", "main_line", "params", "raises",
                                                               "branches", "bases"}}} if e.attrs else {})}


class _Index:
    """Lookup tables for name resolution."""

    def __init__(self, parses: dict[str, ParseResult]):
        self.by_qn: dict[str, Entity] = {}
        self.by_id: dict[str, Entity] = {}
        self.modules: dict[str, list[Entity]] = defaultdict(list)  # dotted-name variants -> module entities
        self.module_of_path: dict[str, Entity] = {}
        self.by_name_in_file: dict[tuple[str, str], list[Entity]] = defaultdict(list)
        for pr in parses.values():
            for e in pr.entities:
                self.by_id[e.id] = e
                if e.type == "Module":
                    self.module_of_path[e.path] = e
                    parts = e.qualified_name.split(".")
                    for i in range(len(parts)):
                        self.modules[".".join(parts[i:])].append(e)
                elif e.type not in {"Endpoint", "ConfigKey", "DeploymentArtifact"} and pr.language == "python":
                    self.by_qn[e.qualified_name] = e
                self.by_name_in_file[(e.path, e.name)].append(e)
        self.paths = set(parses)

    def module(self, dotted: str) -> Entity | None:
        cands = self.modules.get(dotted, [])
        full = [m for m in cands if m.qualified_name == dotted]
        if full:
            return full[0]
        return cands[0] if len(cands) == 1 else None

    def symbol(self, dotted: str) -> Entity | None:
        if dotted in self.by_qn:
            return self.by_qn[dotted]
        mod_name, _, sym = dotted.rpartition(".")
        if not mod_name:
            return None
        m = self.module(mod_name)
        if m is not None:
            return self.by_qn.get(f"{m.qualified_name}.{sym}")
        return None


def _resolve_relative_path(from_path: str, spec: str, paths: set[str]) -> str | None:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
    for cand in [base] + [base + ext for ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".py", ".java", ".go")] + \
            [posixpath.join(base, "index" + ext) for ext in (".js", ".ts", ".jsx", ".tsx")]:
        if cand in paths:
            return cand
    return None


def build_graph(repo_id: str, revision: str, files: dict[str, dict], parses: dict[str, ParseResult]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph(repo_id=repo_id, revision=revision, unresolved_calls=[], external_imports={},
                        stats={"external_calls": 0})
    idx = _Index(parses)
    repo_node = f"repo:{repo_id}"
    g.add_node(repo_node, type="Repository", name=repo_id, path="", extraction_method="manifest", confidence="exact")

    # ---- containment: directories and files
    for path, info in sorted(files.items()):
        parent = repo_node
        parts = PurePosixPath(path).parts
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            did = f"dir:{d}"
            if did not in g:
                g.add_node(did, type="Directory", name=parts[i - 1], path=d, extraction_method="manifest", confidence="exact")
                _add_edge(g, parent, did, "CONTAINS", "manifest")
            parent = did
        fid = f"file:{path}"
        pr = parses.get(path)
        g.add_node(fid, type="File", name=parts[-1], path=path, language=info.get("language", ""),
                   extraction_method=pr.parser if pr else "none", confidence="exact",
                   parse_status=pr.status if pr else "unparsed", sha256=info.get("sha256", ""), lines=info.get("lines", 0))
        _add_edge(g, parent, fid, "CONTAINS", "manifest")

    def nid(e: Entity) -> str:
        return f"config:{e.name}" if e.type == "ConfigKey" else e.id

    # ---- entities
    for path, pr in parses.items():
        fid = f"file:{path}"
        if fid not in g:
            continue
        for e in pr.entities:
            n = nid(e)
            if n not in g:
                g.add_node(n, **_node_attrs(e))
            elif e.type == "ConfigKey":
                g.nodes[n].update(_node_attrs(e))
            parent = nid(idx.by_id[e.parent_id]) if e.parent_id in idx.by_id else fid
            if e.type == "Module":
                parent = fid
            _add_edge(g, parent, n, "CONTAINS", e.extraction_method, path, e.start_line,
                      "resolved" if e.confidence == "exact" else "heuristic")

    def ensure_config(key: str) -> str:
        n = f"config:{key}"
        if n not in g:
            g.add_node(n, type="ConfigKey", name=key, qualified_name=f"config:{key}", path="", extraction_method="usage",
                       confidence="heuristic", note="referenced in code; no declaring file found")
        return n

    # ---- relations
    class_scope: dict[str, Entity] = {}
    for pr in parses.values():
        for e in pr.entities:
            if e.type in {"Class", "DatabaseEntity", "Test"} and pr.language == "python":
                class_scope[e.id] = e

    def enclosing_class(e: Entity) -> Entity | None:
        cur = idx.by_id.get(e.parent_id) if e.parent_id else None
        while cur is not None:
            if cur.id in class_scope:
                return cur
            cur = idx.by_id.get(cur.parent_id) if cur.parent_id else None
        return None

    def resolve_py_call(src: Entity, pr: ParseResult, raw: str) -> tuple[Entity | None, str, str]:
        """Returns (target, method, status) where status in resolved/external/unresolved."""
        parts = raw.split(".")
        head = parts[0]
        if head == "<dynamic>":
            return None, "", "unresolved"
        if head in {"self", "cls"} and len(parts) == 2:
            cls = enclosing_class(src) if src.type != "Module" else None
            if cls is not None:
                t = idx.by_qn.get(f"{cls.qualified_name}.{parts[1]}")
                if t:
                    return t, "ast+class-scope", "resolved"
                for r in pr.relations:  # one level of statically known inheritance
                    if r.src == cls.id and r.type == "INHERITS":
                        b = _resolve_name(r.dst, pr)
                        if b is not None:
                            t = idx.by_qn.get(f"{b.qualified_name}.{parts[1]}")
                            if t:
                                return t, "ast+inheritance", "resolved"
            return None, "", "unresolved"
        if len(parts) == 1:
            scope = src.qualified_name if src.type != "Module" else ""
            while scope:  # nested function scopes
                t = idx.by_qn.get(f"{scope}.{head}")
                if t:
                    return t, "ast+lexical-scope", "resolved"
                scope = scope.rpartition(".")[0]
        local = idx.by_qn.get(f"{pr.module_name}.{raw}")
        if local:
            return local, "ast+module-scope", "resolved"
        if head in pr.import_map:
            full = pr.import_map[head] + ("." + ".".join(parts[1:]) if len(parts) > 1 else "")
            t = idx.symbol(full)
            if t:
                return t, "ast+import-map", "resolved"
            top = pr.import_map[head].split(".")[0]
            if idx.module(pr.import_map[head]) is None and not idx.modules.get(top):
                return None, "", "external"
            return None, "", "unresolved"
        if len(parts) <= 2 and head in _BUILTINS and head not in pr.import_map:
            return None, "", "external"  # Python builtin (len, print, str.join, ...)
        return None, "", "unresolved"

    def _resolve_name(raw: str, pr: ParseResult) -> Entity | None:
        parts = raw.split(".")
        t = idx.by_qn.get(f"{pr.module_name}.{raw}")
        if t:
            return t
        if parts[0] in pr.import_map:
            return idx.symbol(pr.import_map[parts[0]] + ("." + ".".join(parts[1:]) if len(parts) > 1 else ""))
        return None

    for path, pr in parses.items():
        if f"file:{path}" not in g:
            continue
        for r in pr.relations:
            src = idx.by_id.get(r.src)
            if src is None:
                continue
            s = nid(src)
            if r.type == "IMPORTS":
                _resolve_import(g, idx, pr, src, r)
            elif r.type == "CALLS":
                if pr.language == "python":
                    tgt, method, status = resolve_py_call(src, pr, r.dst)
                    if tgt is not None:
                        _add_edge(g, s, tgt.id, "CALLS", method, path, r.line, "resolved")
                        if tgt.type == "DatabaseEntity":
                            _add_edge(g, s, tgt.id, "WRITES_ENTITY", "constructor-call-on-model", path, r.line,
                                      "heuristic")
                    elif status == "external":
                        g.graph["stats"]["external_calls"] += 1
                    elif len(g.graph["unresolved_calls"]) < MAX_UNRESOLVED:
                        g.graph["unresolved_calls"].append({"src": s, "name": r.dst, "path": path, "line": r.line})
                else:  # non-Python: only a unique same-file definition is accepted, and only as heuristic
                    tail = r.dst.split(".")[-1]
                    c = [x for x in idx.by_name_in_file.get((path, tail), []) if x.type in {"Function", "Method", "Class"}
                         and x.id != src.id]
                    if len(c) == 1 and ("." not in r.dst or r.dst.startswith(("this.", "self."))):
                        _add_edge(g, s, c[0].id, "CALLS", "same-file-name-match", path, r.line, "heuristic")
                    elif len(g.graph["unresolved_calls"]) < MAX_UNRESOLVED:
                        g.graph["unresolved_calls"].append({"src": s, "name": r.dst, "path": path, "line": r.line})
            elif r.type in {"INHERITS", "IMPLEMENTS"}:
                t = _resolve_name(r.dst, pr) if pr.language == "python" else None
                if t is None and pr.language != "python":
                    c = [x for x in idx.by_name_in_file.get((path, r.dst.split(".")[-1]), []) if x.type == "Class"]
                    t = c[0] if len(c) == 1 else None
                    if t is not None:
                        _add_edge(g, s, t.id, r.type, "same-file-name-match", path, r.line, "heuristic")
                        continue
                if t is not None:
                    _add_edge(g, s, t.id, r.type, "ast+import-map", path, r.line, "resolved")
                else:
                    g.nodes[s].setdefault("unresolved_bases", []).append(r.dst)
            elif r.type == "READS_CONFIG":
                _add_edge(g, s, ensure_config(r.dst), "READS_CONFIG", r.method or "python-ast:env", path, r.line, "resolved")
            elif r.type == "EXPOSES" and r.dst in idx.by_id:
                _add_edge(g, s, r.dst, "EXPOSES", r.method, path, r.line, "heuristic")
            elif r.type == "DEPLOYS":
                tgt_path = r.dst if r.dst in idx.paths else next((p for p in idx.paths if p.endswith("/" + r.dst)), None)
                if tgt_path and tgt_path in idx.module_of_path:
                    _add_edge(g, s, idx.module_of_path[tgt_path].id, "DEPLOYS", "text-match", path, r.line, "heuristic")

    _add_test_edges(g, idx, parses)
    return g


def _resolve_import(g, idx: _Index, pr: ParseResult, src: Entity, r) -> None:
    s = src.id
    if pr.language == "python":
        mod = idx.module(r.dst) if r.dst else None
        names = r.extra.get("names", [])
        linked = False
        for name in names:
            t = idx.symbol(f"{r.dst}.{name}") if r.dst else None
            sub = idx.module(f"{r.dst}.{name}") if r.dst else None
            if t is not None:
                _add_edge(g, s, t.id, "IMPORTS", "ast", r.path, r.line, "resolved", name=name)
                linked = True
            elif sub is not None:
                _add_edge(g, s, sub.id, "IMPORTS", "ast", r.path, r.line, "resolved", name=name)
                linked = True
        if mod is not None:
            _add_edge(g, s, mod.id, "IMPORTS", "ast", r.path, r.line, "resolved")
        elif not linked and r.dst:
            top = r.dst.split(".")[0]
            ext = f"ext:{top}"
            if ext not in g:
                g.add_node(ext, type="Module", name=top, path="", external=True, extraction_method="import-statement",
                           confidence="exact")
            _add_edge(g, s, ext, "IMPORTS", "ast", r.path, r.line, "external")
            g.graph["external_imports"][top] = g.graph["external_imports"].get(top, 0) + 1
        return
    spec = r.dst
    if r.extra.get("relative"):
        tp = _resolve_relative_path(r.path, spec, idx.paths)
        if tp and tp in idx.module_of_path:
            _add_edge(g, s, idx.module_of_path[tp].id, "IMPORTS", r.method, r.path, r.line, "heuristic")
            return
    ext = f"ext:{spec}"
    if ext not in g:
        g.add_node(ext, type="Module", name=spec, path="", external=True, extraction_method="import-statement",
                   confidence="exact")
    _add_edge(g, s, ext, "IMPORTS", r.method, r.path, r.line, "external")
    top = spec.split("/")[0]
    g.graph["external_imports"][top] = g.graph["external_imports"].get(top, 0) + 1


def _add_test_edges(g, idx: _Index, parses: dict[str, ParseResult]) -> None:
    """TESTS edges: (1) test calls a resolved non-test symbol -> resolved; (2) test_<name> naming in imported
    modules when no call evidence exists -> heuristic."""
    tests = [n for n, d in g.nodes(data=True) if d.get("type") == "Test" and d.get("language") == "python"]
    for t in tests:
        had = False
        for _, v, d in list(g.out_edges(t, data=True)):
            if d["type"] == "CALLS" and g.nodes[v].get("type") in {"Function", "Method", "Class", "DatabaseEntity"}:
                _add_edge(g, t, v, "TESTS", "static-call-from-test", d["path"], d["line"], "resolved")
                had = True
        if had:
            continue
        name = g.nodes[t]["name"]
        if not name.startswith("test_"):
            continue
        base = name[5:]
        tpath = g.nodes[t]["path"]
        mod = idx.module_of_path.get(tpath)
        if mod is None:
            continue
        for _, v, d in g.out_edges(mod.id, data=True):
            if d["type"] == "IMPORTS" and g.nodes[v].get("type") == "Module" and not g.nodes[v].get("external"):
                for _, c, cd in g.out_edges(v, data=True):
                    if cd["type"] == "CONTAINS" and g.nodes[c].get("name") == base:
                        _add_edge(g, t, c, "TESTS", "test-naming-convention", tpath, g.nodes[t]["start_line"], "heuristic")
