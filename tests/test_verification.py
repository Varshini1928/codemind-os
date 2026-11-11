from pathlib import Path

from retrieval.evidence_assembler import EvidenceRegistry
from verification.citation_checker import Citation, CitationChecker
from verification.evidence_validator import Claim, verify_claims


def _setup(indexed):
    svc, rid, root = indexed
    repo = svc.indexing.status(rid)
    reg = EvidenceRegistry(rid, repo["revision"], svc.chunks)
    chk = CitationChecker(rid, Path(repo["root"]), repo["revision"], svc.chunks)
    return svc, rid, root, repo, reg, chk


def test_valid_citation(indexed):
    svc, rid, root, repo, reg, chk = _setup(indexed)
    ch = svc.chunks.for_path(rid, "inventory/service.py")[1]
    r = chk.check(Citation(repo["revision"], "inventory/service.py", ch["start_line"], ch["end_line"], ch["symbol"], ch["id"]))
    assert r.valid and all(r.checks.values())
    assert r.to_dict()["semantic_correctness"] == "not verified"


def test_invalid_citations(indexed):
    svc, rid, root, repo, reg, chk = _setup(indexed)
    rev = repo["revision"]
    assert not chk.check(Citation(rev, "nope/missing.py", 1, 2)).valid
    r = chk.check(Citation(rev, "inventory/service.py", 1, 9999))
    assert not r.valid and "outside file" in r.reason
    assert not chk.check(Citation("deadbeef+0000", "inventory/service.py", 1, 3)).checks["revision_matches"]
    assert not chk.check(Citation(rev, "../../etc/passwd", 1, 1)).valid
    assert not chk.check(Citation(rev, "inventory/service.py", 1, 3, chunk_id="bogus")).valid


def test_stale_file_detected(indexed):
    svc, rid, root, repo, reg, chk = _setup(indexed)
    ch = svc.chunks.for_path(rid, "inventory/models.py")[0]
    (root / "inventory" / "models.py").write_text("# rewritten after indexing\n" * 30)
    r = chk.check(Citation(repo["revision"], "inventory/models.py", ch["start_line"], ch["end_line"], ch["symbol"], ch["id"]))
    assert not r.valid and "differs" in r.reason


def test_claim_handling(indexed):
    svc, rid, root, repo, reg, chk = _setup(indexed)
    b = svc.searcher.search(rid, "apply_discount")
    eid = reg.add(b.items[0])
    claims = [Claim("`apply_discount` computes a price", "fact", [eid]),
              Claim("The system uses Kafka", "fact", []),
              Claim("This should be refactored", "recommendation", []),
              Claim("Mentions `ghost_function` here", "fact", [eid]),
              Claim("Bad ids", "fact", ["E999"])]
    out, stats = verify_claims(claims, reg, chk)
    by = {c.text: c for c in out}
    assert by["`apply_discount` computes a price"].status == "citation-valid"
    assert by["The system uses Kafka"].status == "unverified-hypothesis" and by["The system uses Kafka"].kind == "interpretation"
    assert by["This should be refactored"].status == "uncited-recommendation"
    assert by["Mentions `ghost_function` here"].status == "partially-supported"
    assert by["Bad ids"].status == "unverified-hypothesis"
    assert stats["citation_valid"] == 2 and "does not prove" in stats["note"]
    out2, stats2 = verify_claims([Claim("unsupported", "fact", [])], reg, chk, strict=True)
    assert out2 == [] and stats2["removed"] == 1


def test_invalid_inline_citation_flagged(indexed):
    svc, rid, root, repo, reg, chk = _setup(indexed)
    eid = reg.add(svc.searcher.search(rid, "apply_discount").items[0])
    out, _ = verify_claims([Claim("See fake.py:1-5 for details", "fact", [eid])], reg, chk)
    assert any("invalid inline citation" in n for n in out[0].notes) and out[0].status != "citation-valid"
