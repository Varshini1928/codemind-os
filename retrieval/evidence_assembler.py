"""Evidence items, the per-run evidence registry and token-budget-aware context selection."""
from __future__ import annotations

from dataclasses import dataclass, field

from retrieval.chunk_store import ChunkStore


@dataclass
class EvidenceItem:
    id: str
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str
    language: str
    revision: str
    text: str
    methods: list[str] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    final_score: float = 0.0
    entity_ids: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)

    def citation(self) -> dict:
        return {"revision": self.revision, "path": self.path, "start_line": self.start_line,
                "end_line": self.end_line, "symbol": self.symbol, "chunk_id": self.chunk_id}

    def label(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def to_dict(self, include_text: bool = False) -> dict:
        d = {"id": self.id, **self.citation(), "language": self.language, "methods": self.methods,
             "scores": self.scores, "final_score": round(self.final_score, 4), "entity_ids": self.entity_ids,
             "uncertainty": self.uncertainty}
        if include_text:
            d["text"] = self.text
        return d


@dataclass
class EvidenceBundle:
    repo_id: str
    revision: str
    query: str
    intent: str
    mode: str
    items: list[EvidenceItem]
    uncertainty: list[str] = field(default_factory=list)
    sufficient: bool = True
    stats: dict = field(default_factory=dict)

    def to_dict(self, include_text: bool = False) -> dict:
        return {"repo_id": self.repo_id, "revision": self.revision, "query": self.query, "intent": self.intent,
                "mode": self.mode, "sufficient": self.sufficient, "uncertainty": self.uncertainty,
                "stats": self.stats, "items": [i.to_dict(include_text) for i in self.items]}


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 16


def select_within_budget(items: list[EvidenceItem], budget_tokens: int) -> list[EvidenceItem]:
    chosen, used = [], 0
    for it in items:
        cost = estimate_tokens(it.text)
        if chosen and used + cost > budget_tokens:
            continue
        chosen.append(it)
        used += cost
    return chosen


class EvidenceRegistry:
    """Assigns stable ids (E1, E2, ...) to evidence for one analysis run so every claim can cite them."""

    def __init__(self, repo_id: str, revision: str, chunks: ChunkStore):
        self.repo_id, self.revision, self.chunks = repo_id, revision, chunks
        self.items: dict[str, EvidenceItem] = {}
        self._key: dict[tuple, str] = {}

    def add(self, item: EvidenceItem) -> str:
        key = (item.chunk_id, item.start_line, item.end_line)
        if key in self._key:
            existing = self.items[self._key[key]]
            existing.methods = sorted(set(existing.methods) | set(item.methods))
            return existing.id
        item.id = f"E{len(self.items) + 1}"
        self.items[item.id] = item
        self._key[key] = item.id
        return item.id

    def add_many(self, items: list[EvidenceItem]) -> list[str]:
        return [self.add(i) for i in items]

    def span(self, path: str, start: int, end: int, symbol: str = "", methods: tuple = ("graph",),
             uncertainty: list[str] | None = None) -> str | None:
        ch = self.chunks.chunk_for_line(self.repo_id, path, start)
        if ch is None:
            return None
        end = max(start, min(end, start + 199))
        text = "\n".join(self.chunks.file_lines(self.repo_id, path, start, end))
        return self.add(EvidenceItem("", ch["id"], path, start, end, symbol or ch["symbol"], ch["language"],
                                     self.revision, text, list(methods), {}, 0.0, ch["entity_ids"],
                                     list(uncertainty or [])))

    def get(self, eid: str) -> EvidenceItem | None:
        return self.items.get(eid)

    def all(self) -> list[EvidenceItem]:
        return list(self.items.values())
