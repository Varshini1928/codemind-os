"""Hybrid retrieval: exact + lexical (BM25) + semantic + bounded graph expansion, with documented ranking.

Ranking (see docs/ARCHITECTURE.md):
  Each component is min-max normalised over the candidate set to [0,1]:
    semantic  = cosine similarity of the query and chunk embedding
    graph     = max over seeds of (1 / (1 + hops)) * (1.0 resolved edge | 0.5 heuristic edge); seeds have 1.0
    lexical   = 0.5*exact + 0.5*normalised BM25 when the query names a symbol/file, else normalised BM25
    recency   = file modification time (newer = higher), 0 when all equal
  score = sum(w_i * c_i) / sum(w_i over the components active in the selected mode)
"""
from __future__ import annotations

import time
from typing import Callable

from config.settings import Settings
from graph.dependency_analyzer import INTENT_EDGES, neighbors
from graph.graph_store import GraphStore
from retrieval.chunk_store import ChunkStore
from retrieval.embeddings import Embedder, EmbeddingError
from retrieval.evidence_assembler import EvidenceBundle, EvidenceItem, select_within_budget
from retrieval.lexical_search import BM25Index, exact_matches, query_terms
from retrieval.vector_store import VectorStoreError

MODES = {"lexical": {"lexical"}, "semantic": {"semantic"}, "semantic_graph": {"semantic", "graph"},
         "hybrid": {"semantic", "graph", "lexical", "recency"}}


class IndexNotReadyError(Exception):
    pass


def _norm(d: dict[str, float]) -> dict[str, float]:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: (1.0 if v > 0 else 0.0) for k, v in d.items()}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


class HybridSearcher:
    def __init__(self, settings: Settings, chunks: ChunkStore, vectors, graphs: GraphStore,
                 embedder_factory: Callable[[], Embedder], get_repo: Callable[[str], dict | None]):
        self.s, self.chunks, self.vectors, self.graphs = settings, chunks, vectors, graphs
        self.embedder_factory, self.get_repo = embedder_factory, get_repo
        self._lex: dict[str, tuple[tuple, BM25Index, dict, dict]] = {}

    # ------------------------------------------------------------------ helpers
    def _require_ready(self, repo_id: str) -> dict:
        repo = self.get_repo(repo_id)
        if repo is None:
            raise IndexNotReadyError(f"Unknown repository '{repo_id}'.")
        if repo["status"] != "ready":
            raise IndexNotReadyError(f"Repository index is '{repo['status']}'; run indexing (or a full re-index) first.")
        return repo

    def _lexical(self, repo_id: str, revision: str):
        n = self.chunks.count(repo_id)
        key = (revision, n)
        hit = self._lex.get(repo_id)
        if hit and hit[0] == key:
            return hit[1], hit[2], hit[3]
        rows = self.chunks.all_for_repo(repo_id)
        by_id = {r["id"]: r for r in rows}
        by_entity: dict[str, list[str]] = {}
        for r in rows:
            for e in r["entity_ids"]:
                by_entity.setdefault(e, []).append(r["id"])
        idx = BM25Index(by_id)
        self._lex[repo_id] = (key, idx, by_id, by_entity)
        return idx, by_id, by_entity

    def invalidate(self, repo_id: str) -> None:
        self._lex.pop(repo_id, None)

    # ------------------------------------------------------------------ main entry
    def search(self, repo_id: str, query: str, intent: str = "question", mode: str = "hybrid", k: int | None = None,
               kinds: set[str] | None = None, stale: bool = False, budget: int | None = None) -> EvidenceBundle:
        t0 = time.perf_counter()
        if mode not in MODES:
            raise ValueError(f"Unknown retrieval mode '{mode}'")
        repo = self._require_ready(repo_id)
        if stale and self.s.require_fresh_index:
            raise IndexNotReadyError("The repository changed since indexing and require_fresh_index is enabled.")
        revision = repo["revision"]
        k = k or self.s.top_k
        active = MODES[mode]
        notes: list[str] = []
        if stale:
            notes.append("The working tree changed after indexing; evidence reflects the indexed revision only.")
        bm25, by_id, by_entity = self._lexical(repo_id, revision)
        if kinds:
            by_id_f = {i: r for i, r in by_id.items() if r["kind"] in kinds}
        else:
            by_id_f = by_id
        comps: dict[str, dict[str, float]] = {}
        raw: dict[str, dict[str, float]] = {}
        methods: dict[str, set[str]] = {}

        def put(cid, comp, val, method, rawval=None):
            if cid not in by_id_f:
                return
            comps.setdefault(cid, {})[comp] = max(comps.get(cid, {}).get(comp, 0.0), val)
            raw.setdefault(cid, {})[comp] = max(raw.get(cid, {}).get(comp, -9.0), rawval if rawval is not None else val)
            methods.setdefault(cid, set()).add(method)

        # --- lexical + exact
        if "lexical" in active or mode == "hybrid":
            ex = exact_matches(query, list(by_id_f.values()))
            bm = {c: s for c, s in bm25.scores(query_terms(query)).items() if c in by_id_f}
            bmn = _norm(bm)
            for cid in set(ex) | set(bmn):
                # When the query names an existing symbol/file, exact hits dominate: lexical = 0.5*exact + 0.5*bm25.
                val = (0.5 * ex.get(cid, 0.0) + 0.5 * bmn.get(cid, 0.0)) if ex else bmn.get(cid, 0.0)
                put(cid, "lexical", val, "exact" if cid in ex else "lexical", max(ex.get(cid, 0.0), bm.get(cid, 0.0)))
                if cid in ex:
                    raw[cid]["exact"] = ex[cid]
                    methods[cid].add("lexical") if cid in bmn else None
                if cid in bmn:
                    raw[cid]["bm25"] = bm[cid]
                    methods[cid].add("lexical")
        # --- semantic
        embedder = None
        if "semantic" in active:
            try:
                embedder = self.embedder_factory()
                if repo.get("embedder") and repo["embedder"] != embedder.name:
                    notes.append(f"Index was built with '{repo['embedder']}' but '{embedder.name}' is configured; "
                                 "semantic retrieval skipped - re-index to use the new model.")
                    embedder = None
                else:
                    if not embedder.is_semantic:
                        notes.append("Semantic signal uses the hashing fallback embedder (vocabulary overlap, not meaning).")
                    pending = self.chunks.pending_count(repo_id)
                    if pending:
                        notes.append(f"{pending} chunks have no embedding (embedding errors during indexing).")
                    hits = self.vectors.query(repo_id, embedder.embed_query(query), max(60, 5 * k))
                    sem = {cid: max(0.0, s) for cid, s in hits if cid in by_id_f}
                    for cid, v in _norm(sem).items():
                        put(cid, "semantic", v, "semantic", sem[cid])
                        raw[cid]["cosine"] = sem[cid]
            except (EmbeddingError, VectorStoreError) as e:
                notes.append(f"Semantic retrieval unavailable ({e}); using remaining signals.")
        # --- graph expansion
        graph = None
        if "graph" in active:
            try:
                graph = self.graphs.load(repo_id)
            except Exception as e:
                notes.append(f"Graph expansion unavailable ({type(e).__name__}).")
        if graph is not None and comps:
            prelim = {c: sum(v.values()) / max(1, len(v)) for c, v in comps.items()}
            seeds = sorted(prelim, key=lambda c: -prelim[c])[:5]
            seed_nodes: list[str] = []
            for c in seeds:
                eids = [e for e in by_id[c]["entity_ids"] if e in graph and graph.nodes[e].get("type") != "Module"]
                seed_nodes += eids or [e for e in by_id[c]["entity_ids"] if e in graph]
            seed_nodes = list(dict.fromkeys(seed_nodes))
            etypes = INTENT_EDGES.get(intent, INTENT_EDGES["question"]) - {"CONTAINS"} \
                if intent != "architecture" else INTENT_EDGES["architecture"]
            reached = {n: (0, "seed", "resolved") for n in seed_nodes}
            for r in neighbors(graph, seed_nodes, "both", etypes, min(self.s.max_graph_depth, 3), 300):
                reached[r["node"]] = (r["depth"], r["via"], r["resolution"])
            for node, (depth, via, res) in reached.items():
                val = (1.0 / (1 + depth)) * (1.0 if res == "resolved" else 0.5)
                for cid in by_entity.get(node, []):
                    first = cid not in comps
                    put(cid, "graph", val, "graph", val)
                    if first and depth > 0:
                        methods[cid].add("graph-only")
                        raw[cid]["graph_via"] = 0.0
                        notes_key = f"via {via} ({res}), {depth} hop(s)"
                        raw[cid]["_why"] = notes_key  # type: ignore[assignment]
        # --- recency
        rec = _norm({c: by_id[c]["file_mtime"] or 0.0 for c in comps}) if "recency" in active else {}
        # --- combine
        weights = {k_: w for k_, w in self.s.weights().items() if k_ in active}
        wsum = sum(weights.values()) or 1.0
        normed: dict[str, dict[str, float]] = {}
        for comp in ("semantic", "graph", "lexical"):
            normed[comp] = _norm({c: v[comp] for c, v in comps.items() if comp in v}) if comp != "lexical" else \
                {c: v["lexical"] for c, v in comps.items() if "lexical" in v}
        scored = []
        for cid in comps:
            sc = {"semantic": normed["semantic"].get(cid, 0.0), "graph": comps[cid].get("graph", 0.0),
                  "lexical": normed["lexical"].get(cid, 0.0), "recency": rec.get(cid, 0.0)}
            final = sum(weights[c] * sc[c] for c in weights) / wsum
            scored.append((final, cid, sc))
        scored.sort(key=lambda t: (-t[0], t[1]))
        # --- dedupe overlapping chunks, then top-k
        picked: list[tuple[float, str, dict]] = []
        for final, cid, sc in scored:
            r = by_id[cid]
            clash = False
            for _, pid, _ in picked:
                p = by_id[pid]
                if p["path"] == r["path"]:
                    ov = min(p["end_line"], r["end_line"]) - max(p["start_line"], r["start_line"]) + 1
                    if ov > 0 and ov / min(p["end_line"] - p["start_line"] + 1, r["end_line"] - r["start_line"] + 1) >= 0.5:
                        clash = True
                        break
            if not clash:
                picked.append((final, cid, sc))
            if len(picked) >= k:
                break
        items = []
        for final, cid, sc in picked:
            r = by_id[cid]
            unc = []
            if "graph-only" in methods[cid]:
                unc.append("Retrieved only through graph expansion " + str(raw[cid].get("_why", "")))
            fnode = "file:" + r["path"]
            if graph is not None and fnode in graph:
                pstatus = graph.nodes[fnode].get("parse_status")
                if pstatus in {"partial", "syntax_error", "text_only"}:
                    unc.append("File parse status: " + str(pstatus) + " (heuristic structure)")
            rs = {k_: round(v, 4) for k_, v in raw[cid].items() if not k_.startswith("_") and isinstance(v, float)}
            items.append(EvidenceItem("", cid, r["path"], r["start_line"], r["end_line"], r["symbol"], r["language"],
                                      revision, r["text"], sorted(methods[cid]),
                                      {"components": {k_: round(v, 4) for k_, v in sc.items()}, "raw": rs},
                                      final, r["entity_ids"], unc))
        items = select_within_budget(items, budget or self.s.context_token_budget)
        for n, it in enumerate(items, 1):
            it.id = f"R{n}"
        sufficient = any(
            it.scores["raw"].get("exact", 0) > 0 or it.scores["raw"].get("bm25", 0) >= self.s.min_bm25
            or (embedder is not None and embedder.is_semantic and it.scores["raw"].get("cosine", 0) >= self.s.min_cosine)
            for it in items)
        if not items:
            notes.append("No matching evidence was found in the index.")
        return EvidenceBundle(repo_id, revision, query, intent, mode, items, notes, sufficient,
                              {"candidates": len(comps), "returned": len(items), "weights": weights,
                               "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)})
