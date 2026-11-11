"""Tokenisation, BM25 and exact symbol/file matching (all local, no dependencies)."""
from __future__ import annotations

import math
import re
from collections import Counter

_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
STOPWORDS = set("""a an and are as at be by can do does for from how i if in into is it its of on or that the this to
use used what when where which who why with without about does did you your we our there their they them than then
show tell explain describe list give find me my please code repository repo project function class method file
files work works working happens happen change changed changes affect affected impact""".split())


def split_identifier(tok: str) -> list[str]:
    parts: list[str] = []
    for chunk in _SPLIT.split(tok):
        if not chunk:
            continue
        parts += [p.lower() for p in _CAMEL.split(chunk) if p]
    return parts


def tokenize(text: str) -> list[str]:
    """Lowercased tokens; identifiers are emitted whole and as sub-tokens (getUserName -> getusername, get, user, name)."""
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        low = raw.lower()
        parts = split_identifier(raw)
        if len(parts) > 1 or "_" in raw:
            out.append(low.replace("_", ""))
        out += parts if parts else [low]
    return [t for t in out if len(t) > 1]


def query_terms(query: str) -> list[str]:
    return [t for t in tokenize(query) if t not in STOPWORDS]


def identifier_candidates(query: str) -> list[str]:
    """Words in the query that look like code identifiers or paths (kept case-sensitive for exact matching)."""
    found = re.findall(r"`([^`]+)`", query)
    found += re.findall(r"[\w./\\-]+\.\w{1,5}\b", query)
    found += [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query)
              if w.lower() not in STOPWORDS and len(w) > 2]
    seen, out = set(), []
    for f in found:
        f = f.strip().replace("\\", "/")
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


class BM25Index:
    def __init__(self, docs: dict[str, dict], k1: float = 1.5, b: float = 0.75):
        """docs: chunk_id -> row with text/path/symbol."""
        self.k1, self.b = k1, b
        self.tf: dict[str, Counter] = {}
        self.len: dict[str, int] = {}
        self.df: Counter = Counter()
        for cid, d in docs.items():
            toks = tokenize(d["text"]) + (tokenize(d.get("symbol", "")) + tokenize(d["path"])) * 3
            c = Counter(toks)
            self.tf[cid], self.len[cid] = c, len(toks)
            self.df.update(c.keys())
        self.n = len(docs)
        self.avg = (sum(self.len.values()) / self.n) if self.n else 1.0

    def scores(self, terms: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in set(terms):
            df = self.df.get(t, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for cid, c in self.tf.items():
                f = c.get(t, 0)
                if f:
                    out[cid] = out.get(cid, 0.0) + idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[cid] / self.avg))
        return out


def exact_matches(query: str, chunks: list[dict]) -> dict[str, float]:
    """Exact (or near-exact) symbol and file-name matches. Score in (0, 1]."""
    cands = identifier_candidates(query)
    if not cands:
        return {}
    out: dict[str, float] = {}
    for c in chunks:
        sym = c.get("symbol", "")
        qn_last = sym.split(" (")[0].split(".")[-1]
        path = c["path"]
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        best = 0.0
        for t in cands:
            tl = t.lower()
            if "/" in t or re.search(r"\.\w{1,5}$", t) and t.lower() == path.lower().rsplit("/", 1)[-1]:
                if path.lower() == tl or path.lower().endswith("/" + tl):
                    best = max(best, 1.0 if sym.startswith("<") else 0.7)
            if qn_last and qn_last == t:
                best = max(best, 1.0)
            elif sym.split(" (")[0] == t or sym.split(" (")[0].endswith("." + t):
                best = max(best, 1.0)
            elif qn_last and qn_last.lower() == tl:
                best = max(best, 0.85)
            elif stem.lower() == tl and len(tl) > 3:
                best = max(best, 0.6)
        if best:
            out[c["id"]] = best
    return out
