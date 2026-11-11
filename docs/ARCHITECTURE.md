# Architecture

```
            ┌──── Web UI (web/, plain JS, served by FastAPI) ── FastAPI /api (backend/api.py) ── CLI (main.py)
            │            the browser talks to the API; API and CLI call the same in-process services
            ▼
  services/  indexing · analysis · reports · jobs · test_execution (opt-in)
     │
     ├─ ingestion/   loader → file_filter → manifest → incremental_indexer
     ├─ parsing/     python_parser (ast) · treesitter_parser (optional) · text_fallback (regex, heuristic)
     ├─ graph/       graph_builder (NetworkX) · graph_store (JSON) · dependency_analyzer (bounded traversal, impact)
     ├─ retrieval/   chunker → chunk_store (SQLite) + embeddings → vector_store (Chroma | SQLite+NumPy)
     │               lexical_search (BM25, exact) · hybrid_search · evidence_assembler
     ├─ agents/      workflow (LangGraph) → analyst · quality · impact · tests · docs ; llm_client (Ollama)
     └─ verification/ safety_policy · citation_checker · evidence_validator
```

## Indexing pipeline (read-only)
1. **Manifest**: walk the repository (symlinks never followed), apply exclusions, secret-filename and secret-content checks, size/binary checks, SHA-256 per file. Revision id = `git HEAD[:12] + "+" + digest(indexed contents)[:10]`, so uncommitted edits get a different revision than their commit (`nogit+…` outside Git).
2. **Diff** against the stored manifest: added / modified / deleted / unchanged.
3. **Parse** only changed files; per-file `ParseResult` JSON is cached in SQLite. Syntax errors never abort a run (regex fallback, status `syntax_error`).
4. **Graph** is rebuilt deterministically from all cached parses (cross-file resolution is global, so a changed file can alter edges elsewhere; re-resolving everything is the simplest correct invalidation). Only changed files are re-parsed.
5. **Chunks**: symbol-aware for code (function/class boundaries; big classes split into methods; module-level gaps kept), heading-aware for Markdown, block-aware for YAML/TOML/INI/Dockerfile, line windows otherwise. Chunk id = hash(repo, path, range, content hash); unchanged chunks keep their id and embedding.
6. **Embeddings** for chunks without one → ChromaDB (or SQLite+NumPy). Embedding failures leave chunks pending and are reported; lexical and graph retrieval continue to work.
7. The repository row switches to `ready` only at the end. While `indexing`, `failed` or `cancelled`, queries are refused (no mixing of revisions). SQLite is the authority for chunk revisions; unchanged chunks are re-stamped with the new revision after a successful run. A stat-only signature detects a stale index cheaply.

## Knowledge graph
Nodes: Repository, Directory, File, Module, Class, Function, Method, Test, Endpoint, ConfigKey, DatabaseEntity, DeploymentArtifact (+ external Module nodes). Edges: CONTAINS, IMPORTS, CALLS, INHERITS, IMPLEMENTS, READS_CONFIG, WRITES_ENTITY, TESTS, EXPOSES, DEPLOYS. Each edge stores `method`, `path`, `line`, `resolution` ∈ {resolved, heuristic, external} and a numeric confidence.

| Edge | How it is derived | Resolution |
|---|---|---|
| IMPORTS/CALLS/INHERITS (Python) | `ast` + import map + lexical/class scope + one inheritance level | resolved |
| CALLS (other languages) | unique same-file name only | heuristic |
| TESTS | resolved call from a test, else `test_<name>` naming in imported modules | resolved / heuristic |
| EXPOSES | route decorator pattern (`@x.get("/p")`) — framework not verified | heuristic |
| READS_CONFIG | `os.getenv/os.environ` with literal key | resolved |
| WRITES_ENTITY | call of a model-like class constructor | heuristic |
| DEPLOYS | file name mentioned in Dockerfile/compose/workflow text | heuristic |

Unresolved call sites (dynamic receivers, `obj.method()`, etc.) are **never** turned into edges; they are listed in `graph.graph["unresolved_calls"]` and surfaced in impact reports as unconfirmed possible callers. Name similarity alone never creates an edge. No LLM output is ever written to the graph.

## Hybrid ranking
Components per candidate chunk, each min–max normalised over the candidate set:
* **semantic** – cosine(query, chunk embedding)
* **graph** – seeds (top-5 preliminary hits) score 1.0; nodes reached by intent-specific bounded traversal score `1/(1+hops) × (1.0 resolved | 0.5 heuristic)`
* **lexical** – `0.5·exact + 0.5·BM25` when the query names an existing symbol/file, else BM25
* **recency** – file modification time

`score = Σ wᵢ·cᵢ / Σ wᵢ` over components active in the selected mode (`lexical`, `semantic`, `semantic_graph`, `hybrid`); defaults α=.45, β=.20, γ=.30, δ=.05. Overlapping chunks (≥50%) are de-duplicated, then a token budget trims the context. Intent decides traversal edges: architecture → containment/imports/endpoints/deploy/config; question → calls/imports/inheritance; impact → reverse calls, inheritance, config, tests; tests → tests/calls/imports; documentation → containment/imports/endpoints/config.
Evidence is "sufficient" only if some item has an exact match, BM25 ≥ `min_bm25`, or (neural embeddings only) cosine ≥ `min_cosine`; otherwise the answer is *insufficient evidence*. These thresholds are heuristics.

## Workflow (LangGraph)
`classify → plan (decompose ≤4 sub-questions) → select ⇄ {analyst | quality | impact | tests | docs} → verify → assemble`. Routing is conditional per sub-task; a deadline guards the whole run; a failing stage becomes a recorded error and a `partial` result. Intent classification is rule-based (no LLM needed). If `langgraph` is not installed the same node functions run in a sequential engine and the result states `engine`.

## Verification
Evidence ids (`E1…`) are assigned per run. Each claim is `fact`, `interpretation` or `recommendation`. `CitationChecker` validates: path exists in the repo and index, line range valid, revision equals the indexed revision, chunk covers the range and matches the file on disk. `verify_claims` relabels unsupported facts as *unverified hypotheses* (or removes them in `strict_claims` mode), flags invalid inline `path:a-b` citations, and notes backticked identifiers absent from the cited lines. **Citation validity is not semantic correctness**; reports say so.
