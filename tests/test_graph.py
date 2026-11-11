from graph.dependency_analyzer import impact_analysis, neighbors, resolve_target
from graph.graph_builder import build_graph
from graph.graph_store import GraphCorruptError, GraphStore, from_json_dict, to_json_dict
from parsing.entity_extractor import parse_file
import pytest


def _graph(tiny_repo):
    files = {str(p.relative_to(tiny_repo)).replace("\\", "/"): p.read_text() for p in tiny_repo.rglob("*.py")}
    parses = {k: parse_file(k, v) for k, v in files.items()}
    return build_graph("r", "rev", {k: {"language": "python", "lines": 1, "sha256": "x"} for k in files}, parses)


def _q(g, qn):
    return next(n for n, d in g.nodes(data=True) if d.get("qualified_name") == qn)


def test_edges_resolved_with_evidence(tiny_repo):
    g = _graph(tiny_repo)
    step, helper, run = _q(g, "pkg.core.Engine.step"), _q(g, "pkg.util.helper"), _q(g, "pkg.core.Engine.run")
    edges = {(u, v, d["type"]): d for u, v, d in g.edges(data=True)}
    assert edges[(step, helper, "CALLS")]["resolution"] == "resolved"
    assert edges[(step, helper, "CALLS")]["method"] == "ast+import-map" and edges[(step, helper, "CALLS")]["line"] == 9
    assert edges[(run, step, "CALLS")]["method"] == "ast+class-scope"
    assert (_q(g, "pkg.core"), _q(g, "pkg.util"), "IMPORTS") in edges


def test_no_edges_from_similar_names(tiny_repo):
    g = _graph(tiny_repo)
    sim = _q(g, "pkg.core.similar_name")
    assert not [1 for u, v, d in g.edges(data=True) if d["type"] == "CALLS" and sim in (u, v)]
    assert not [1 for u, v, d in g.edges(data=True) if d["type"] == "CALLS" and v == _q(g, "pkg.util.unused")]


def test_unresolved_calls_are_listed_not_edged(tmp_path):
    parses = {"a.py": parse_file("a.py", "def f(obj):\n    obj.something()\n")}
    g = build_graph("r", "v", {"a.py": {"language": "python"}}, parses)
    assert any(u["name"] == "obj.something" for u in g.graph["unresolved_calls"])
    assert not [1 for _, _, d in g.edges(data=True) if d["type"] == "CALLS"]


def test_traversal_depth_bounded_and_impact(tiny_repo):
    g = _graph(tiny_repo)
    helper = _q(g, "pkg.util.helper")
    d1 = neighbors(g, [helper], "in", {"CALLS"}, 1)
    d3 = neighbors(g, [helper], "in", {"CALLS"}, 3)
    assert {g.nodes[r["node"]]["name"] for r in d1} == {"step"}
    assert {"step", "run"} <= {g.nodes[r["node"]]["name"] for r in d3}
    imp = impact_analysis(g, [helper], 3)
    assert any(i["name"] == "step" and i["resolution"] == "resolved" for i in imp["direct_dependents"])
    assert any(i["name"] == "run" for i in imp["transitive_dependents"])
    assert "limits" in imp


def test_resolve_target_ambiguity(tiny_repo):
    g = _graph(tiny_repo)
    assert resolve_target(g, "helper")["kind"] == "symbol"
    assert resolve_target(g, "pkg/util.py")["kind"] == "file"
    assert resolve_target(g, "nonexistent")["kind"] == "unknown"


def test_json_roundtrip_and_corruption(tiny_repo, tmp_path):
    g = _graph(tiny_repo)
    store = GraphStore(tmp_path / "g")
    store.save("r", g)
    g2 = store.load("r")
    assert g2.number_of_nodes() == g.number_of_nodes() and g2.number_of_edges() == g.number_of_edges()
    store.path("r").write_text("{not json")
    store._cache.clear()
    with pytest.raises(GraphCorruptError):
        store.load("r")
    with pytest.raises(GraphCorruptError):
        from_json_dict({"format": "other"})
    assert to_json_dict(g)["format"] == "codemind-graph/1"


def test_sample_graph_has_all_expected_edge_types(indexed):
    svc, rid, _ = indexed
    g = svc.graphs.load(rid)
    types = {d["type"] for _, _, d in g.edges(data=True)}
    assert {"CONTAINS", "IMPORTS", "CALLS", "INHERITS", "READS_CONFIG", "TESTS", "EXPOSES", "DEPLOYS"} <= types
    for _, _, d in g.edges(data=True):
        assert d["method"] and d["resolution"] in {"resolved", "heuristic", "external"}
