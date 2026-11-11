"""Claim verification: every material repository claim must cite evidence; unsupported ones are relabelled or removed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from retrieval.evidence_assembler import EvidenceRegistry
from verification.citation_checker import Citation, CitationChecker, CITATION_RX

KINDS = {"fact", "interpretation", "recommendation"}
_BACKTICK = re.compile(r"`([A-Za-z_][\w./:-]*)`")


@dataclass
class Claim:
    text: str
    kind: str = "fact"  # fact | interpretation | recommendation
    evidence_ids: list[str] = field(default_factory=list)
    status: str = "unchecked"
    notes: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "evidence_ids": self.evidence_ids, "status": self.status,
                "notes": self.notes, "citations": self.citations}


# statuses: citation-valid | unverified-hypothesis | uncited-interpretation | removed
def verify_claims(claims: list[Claim], registry: EvidenceRegistry, checker: CitationChecker,
                  strict: bool = False) -> tuple[list[Claim], dict]:
    kept: list[Claim] = []
    stats = {"total": len(claims), "citation_valid": 0, "unverified": 0, "removed": 0, "uncited": 0,
             "claims_with_evidence": 0}
    for c in claims:
        if c.kind not in KINDS:
            c.kind = "interpretation"
            c.notes.append("unknown claim kind; treated as interpretation")
        ids = [i for i in dict.fromkeys(c.evidence_ids)]
        unknown = [i for i in ids if registry.get(i) is None]
        if unknown:
            c.notes.append(f"unknown evidence ids dropped: {', '.join(unknown)}")
        ids = [i for i in ids if registry.get(i) is not None]
        c.evidence_ids = ids
        # raw citations typed inside the claim text must be valid too
        bad_inline = []
        for cit in checker.find_inline(c.text):
            if not cit.revision:
                cit.revision = checker.rev
            r = checker.check(cit, verify_on_disk=False)
            if not r.valid:
                bad_inline.append(f"{cit.path}:{cit.start_line}-{cit.end_line} ({r.reason})")
        if bad_inline:
            c.notes.append("invalid inline citation(s): " + "; ".join(bad_inline))
        results = []
        for i in ids:
            it = registry.get(i)
            r = checker.check(Citation(it.revision, it.path, it.start_line, it.end_line, it.symbol, it.chunk_id))
            results.append(r)
            c.citations.append(r.to_dict())
        valid = bool(results) and all(r.valid for r in results) and not bad_inline
        if ids:
            stats["claims_with_evidence"] += 1
        if valid:
            # light symbol-support check: identifiers in backticks should appear in the cited text
            blob = "\n".join(registry.get(i).text + " " + registry.get(i).path + " " + registry.get(i).symbol for i in ids)
            missing = [m for m in _BACKTICK.findall(CITATION_RX.sub("", c.text)) if m.split(".")[-1] not in blob]
            if missing:
                c.notes.append("mentioned identifiers not found in the cited lines: " + ", ".join(missing[:5]))
                c.status = "partially-supported"
            else:
                c.status = "citation-valid"
            stats["citation_valid"] += 1
        elif c.kind == "fact":
            if strict:
                c.status = "removed"
                stats["removed"] += 1
                continue
            c.kind, c.status = "interpretation", "unverified-hypothesis"
            c.notes.append("no valid supporting citation: relabelled as an unverified hypothesis")
            stats["unverified"] += 1
        else:
            c.status = "uncited-" + c.kind if not ids else "unverified-hypothesis"
            stats["uncited"] += 1
        kept.append(c)
    stats["citation_coverage"] = round(stats["claims_with_evidence"] / stats["total"], 3) if stats["total"] else None
    stats["note"] = ("Citation validity shows the cited lines exist in the indexed revision; it does not prove "
                     "that a claim's interpretation is correct.")
    return kept, stats
