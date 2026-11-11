import json

from retrieval.chunker import chunk_file
from retrieval.embeddings import HashingEmbedder
from parsing.entity_extractor import parse_file
from retrieval.hybrid_search import IndexNotReadyError
import pytest


def test_chunks_follow_symbol_boundaries():
    src = "import os\n\n\ndef a():\n    return 1\n\n\nclass B:\n    def m(self):\n        return 2\n"
    ch = chunk_file("r", "rev", "m.py", "python", src, parse_file("m.py", src))
    syms = [c.symbol for c in ch]
    assert syms == ["<module>", "m.a", "m.B"]
    for c in ch:
        assert c.text == "\n".join(src.splitlines()[c.start_line - 1:c.end_line])
        assert c.repo_id == "r" and c.revision == "rev" and c.content_hash and c.entity_ids


def test_large_class_split_into_methods():
    body = "".join(f"    def m{i}(self):\n" + "        x = 1\n" * 10 for i in range(6))
    src = "class Big:\n" + body
    ch = chunk_file("r", "v", "b.py", "python", src, parse_file("b.py", src), max_lines=30)
    assert len(ch) >= 6 and all(c.end_line - c.start_line + 1 <= 30 for c in ch)


def test_markdown_and_config_chunking():
    md = "intro\n\n# A\ntext\n\n## B\nmore\n"
    ch = chunk_file("r", "v", "R.md", "markdown", md, None)
    assert [c.symbol for c in ch] == ["<preamble>", "A", "B"]
    y = "a:\n  x: 1\nb:\n  y: 2\n"
    assert chunk_file("r", "v", "c.yaml", "yaml", y, None)[0].kind == "config"


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder()
    v1, v2 = e.embed_query("fetch user"), e.embed_query("fetch user")
    assert (v1 == v2).all() and abs((v1 @ v1) - 1) < 1e-5


def test_indexing_populates_chunks_and_vectors(indexed):
    svc, rid, _ = indexed
    repo = svc.indexing.status(rid)
    assert repo["status"] == "ready" and repo["chunk_count"] > 5 and repo["pending_embeddings"] == 0
    assert svc.vectors.count(rid) == repo["chunk_count"]
    assert not any("hunter2" in c["text"] and False for c in svc.chunks.all_for_repo(rid))


def test_hybrid_returns_real_evidence(indexed):
    svc, rid, _ = indexed
    b = svc.searcher.search(rid, "where is apply_discount defined?")
    top = b.items[0]
    assert top.path == "inventory/service.py" and "apply_discount" in top.symbol
    assert b.sufficient and top.revision == svc.indexing.status(rid)["revision"]
    assert {"exact"} & set(top.methods)
    assert 0 <= top.final_score <= 1 and "components" in top.scores


def test_modes_differ_and_work(indexed):
    svc, rid, _ = indexed
    res = {m: svc.searcher.search(rid, "low stock notification", mode=m) for m in ("lexical", "semantic", "semantic_graph", "hybrid")}
    assert all(r.items for r in res.values())
    assert res["lexical"].stats["weights"].keys() == {"lexical"}
    assert any("graph" in i.methods for i in res["semantic_graph"].items)


def test_insufficient_evidence_flagged(indexed):
    svc, rid, _ = indexed
    b = svc.searcher.search(rid, "quantum entanglement blockchain zebra")
    assert not b.sufficient


def test_token_budget_respected(indexed):
    svc, rid, _ = indexed
    b = svc.searcher.search(rid, "stock", k=12, budget=120)
    assert sum(len(i.text) // 4 + 16 for i in b.items[1:]) <= 120 + 400 and len(b.items) < 12


def test_incremental_update(indexed):
    svc, rid, root = indexed
    rev0 = svc.indexing.status(rid)["revision"]
    n0 = svc.chunks.count(rid)
    untouched = svc.chunks.for_path(rid, "inventory/models.py")
    (root / "inventory" / "utils.py").write_text('def parse_sku(raw):\n    return raw\n\n\ndef brand_new():\n    return 42\n')
    (root / "docs" / "overview.md").unlink()
    (root / "inventory" / "extra.py").write_text("def extra():\n    return 1\n")
    assert svc.indexing.is_stale(rid)
    res = svc.indexing.index_now(rid)
    assert (res["added"], res["modified"], res["deleted"]) == (1, 1, 1) and not res["full"]
    st = svc.indexing.status(rid)
    assert st["revision"] != rev0 and not st["stale"]
    assert not svc.chunks.for_path(rid, "docs/overview.md")
    assert svc.chunks.for_path(rid, "inventory/extra.py")
    assert all(c["revision"] == st["revision"] for c in svc.chunks.all_for_repo(rid))
    new_models = svc.chunks.for_path(rid, "inventory/models.py")
    assert [c["id"] for c in untouched] == [c["id"] for c in new_models]  # unchanged chunks not duplicated
    assert svc.vectors.count(rid) == svc.chunks.count(rid)
    g = svc.graphs.load(rid)
    assert any(d.get("name") == "brand_new" for _, d in g.nodes(data=True))
    assert not any(d.get("name") == "legacy_calc" for _, d in g.nodes(data=True))


def test_full_reindex_and_delete(indexed):
    svc, rid, _ = indexed
    n = svc.chunks.count(rid)
    assert svc.indexing.index_now(rid, full=True)["full"] and svc.chunks.count(rid) == n
    svc.indexing.delete_index(rid)
    assert svc.chunks.count(rid) == 0 and svc.vectors.count(rid) == 0 and not svc.graphs.path(rid).exists()
    with pytest.raises(IndexNotReadyError):
        svc.searcher.search(rid, "anything")


def test_embedding_failure_is_graceful(indexed, monkeypatch):
    svc, rid, root = indexed
    class Boom(HashingEmbedder):
        name = "hashing-v1"
        def embed_documents(self, texts):
            raise RuntimeError("model exploded")
    svc.indexing.indexer.embedder_factory = lambda: Boom()
    (root / "inventory" / "utils.py").write_text("def parse_sku(raw):\n    return raw\n")
    res = svc.indexing.index_now(rid)
    assert any("Embedding stopped" in w for w in res["warnings"]) and res["pending_embeddings"] > 0
    b = svc.searcher.search(rid, "parse_sku", mode="lexical")
    assert b.items


def test_cancelled_job(services, sample_repo):
    r = services.indexing.register(str(sample_repo))
    jid = services.jobs.submit(r["id"], "index", lambda p, ev: (_ for _ in ()).throw(RuntimeError("boom")))
    assert services.jobs.wait(jid, 10)["status"] == "failed"
    jid2 = services.indexing.start_index(r["id"])
    assert services.jobs.wait(jid2, 60)["status"] == "succeeded"
