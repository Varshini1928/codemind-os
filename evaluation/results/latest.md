# Evaluation results (measured)

Environment: `{"python": "3.12.3", "embedder": "hashing-v1", "vector_store": "sqlite-numpy", "semantic_embeddings": false, "repository_revision": "nogit+c2db680b30", "files": 15, "chunks": 39, "queries": 10}`

| Configuration | recall@1 | recall@3 | recall@5 | mrr | query_latency_ms_mean | query_latency_ms_p95 |
|---|---|---|---|---|---|---|
| lexical-only | 0.450 | 0.700 | 0.700 | 0.600 | 1.025 | 4.633 |
| semantic-only | 0.150 | 0.700 | 0.700 | 0.417 | 0.688 | 1.309 |
| semantic+graph | 0.150 | 0.700 | 0.700 | 0.417 | 1.288 | 3.219 |
| full-hybrid | 0.650 | 0.700 | 0.700 | 0.700 | 1.637 | 1.965 |

Citations: `{"path": "deterministic analysis without an LLM", "claims": 60, "citation_coverage": 1.0, "citation_precision": 1.0, "note": "Citation validity != semantic correctness. LLM-path citation quality: not evaluated (run with Ollama and --llm)."}`

Impact (mean): `{"precision": 0.8888888888888888, "recall": 1.0, "precision_resolved_only": 1.0, "recall_resolved_only": 1.0}`

Usefulness: `{"human_rated_usefulness": "not evaluated"}`
Performance: `{"indexing_latency_s": 0.104, "files_indexed": 15, "peak_memory_mb": 59.5, "index_size_kb": 734.2, "unexpected_external_connections": 0, "external_hosts": []}`