"""Portable JSON persistence for the knowledge graph (atomic writes, validated loads)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import networkx as nx


class GraphCorruptError(Exception):
    pass


def to_json_dict(g: nx.MultiDiGraph) -> dict:
    return {
        "format": "codemind-graph/1",
        "graph": {k: v for k, v in g.graph.items()},
        "nodes": [{"id": n, **d} for n, d in g.nodes(data=True)],
        "edges": [{"source": u, "target": v, **d} for u, v, d in g.edges(data=True)],
    }


def from_json_dict(data: dict) -> nx.MultiDiGraph:
    try:
        if data.get("format") != "codemind-graph/1":
            raise GraphCorruptError("Unknown graph format")
        g = nx.MultiDiGraph(**data["graph"])
        for n in data["nodes"]:
            n = dict(n)
            g.add_node(n.pop("id"), **n)
        for e in data["edges"]:
            e = dict(e)
            g.add_edge(e.pop("source"), e.pop("target"), **e)
        return g
    except (KeyError, TypeError, ValueError) as ex:
        raise GraphCorruptError(f"Graph data is malformed: {ex}") from ex


class GraphStore:
    def __init__(self, graphs_dir: Path):
        self.dir = Path(graphs_dir)
        self._cache: dict[str, tuple[float, nx.MultiDiGraph]] = {}

    def path(self, repo_id: str) -> Path:
        return self.dir / f"{repo_id}.json"

    def save(self, repo_id: str, g: nx.MultiDiGraph) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(repo_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(to_json_dict(g)), encoding="utf-8")
        os.replace(tmp, p)
        self._cache.pop(repo_id, None)
        return p

    def load(self, repo_id: str) -> nx.MultiDiGraph:
        p = self.path(repo_id)
        if not p.exists():
            raise FileNotFoundError(f"No graph stored for repository {repo_id}")
        mtime = p.stat().st_mtime
        hit = self._cache.get(repo_id)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            g = from_json_dict(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as ex:
            raise GraphCorruptError(f"Graph file is not valid JSON: {ex}") from ex
        self._cache[repo_id] = (mtime, g)
        return g

    def delete(self, repo_id: str) -> None:
        self._cache.pop(repo_id, None)
        self.path(repo_id).unlink(missing_ok=True)

    def export_json(self, repo_id: str) -> dict:
        return to_json_dict(self.load(repo_id))
