# Evaluation

`python scripts/evaluate.py [--repo PATH] [--dataset FILE] [--embedding auto|hashing]` indexes the repository into a temporary storage directory and measures, for a fixed revision and identical questions:

| Metric | Meaning |
|---|---|
| Recall@k, MRR | against manually verified expected evidence (`path` + `symbol` or line range) |
| Citation precision / coverage | share of claims with evidence / share of cited claims whose citations validate (deterministic path; LLM path **not evaluated** by the script) |
| Impact precision / recall | predicted affected files vs expected files (also "resolved-only" variant) |
| Human-rated usefulness | `ratings: [{"score": 1-5}]` in the dataset; otherwise *not evaluated* |
| Indexing/query latency, peak RSS, index size | wall-clock / `ru_maxrss` / bytes on disk |
| Unexpected external connections | non-loopback `socket.connect` attempts counted during the run |

Configurations: lexical-only, semantic-only, semantic+graph, full hybrid (+ evidence verification for the citation numbers).

## Dataset format (`evaluation/dataset_sample.json`)
`queries[]`: `{id, question, intent, expected:[{path, symbol} | {path, start, end} | {path}]}`; `impact[]`: `{target, expected_files}`; `ratings[]`. Record expectations by reading the code at a fixed revision.

## Measured results (this authoring sandbox)
Run on the bundled synthetic `sample_repo` (15 files, 39 chunks, 10 questions, 3 impact targets), Python 3.12, **hashing embedder (no neural model installed)**, SQLite vector store, no LLM:

| Configuration | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| lexical-only | 0.450 | 0.700 | 0.700 | 0.600 |
| semantic-only (hashing) | 0.150 | 0.700 | 0.700 | 0.417 |
| semantic+graph (hashing) | 0.150 | 0.700 | 0.700 | 0.417 |
| full hybrid | 0.650 | 0.700 | 0.700 | 0.700 |

Citation coverage 1.0 / precision 1.0 over 60 deterministic claims (citation validity only). Impact precision 0.889, recall 1.0 (resolved-only: 1.0 / 1.0) over 3 targets. Indexing 0.08 s, mean query latency ≈1–2 ms, peak RSS ≈60 MB, index 734 KB, 0 external connections.

**How to read this:** the dataset is tiny and synthetic, so these numbers show the harness works and the ranking behaves sensibly — they are *not* evidence of general accuracy. Three of ten questions are paraphrases with no shared vocabulary, which the hashing embedder cannot bridge (recall plateaus at 0.7); a neural embedder is expected to help but **has not been measured here**. Re-run on your own repositories and fixed revisions for meaningful numbers. LLM answer quality, citation precision of LLM claims and human usefulness: **not evaluated**.
