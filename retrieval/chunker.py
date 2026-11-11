"""Symbol-aware chunking. Code splits on function/class boundaries; docs on headings; config on blocks."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from parsing.entity_extractor import Entity, ParseResult, content_hash
from parsing.languages import CODE_LANGS, CONFIG_LANGS, DOC_LANGS

_SKIP_TYPES = {"Module", "Endpoint", "ConfigKey", "DeploymentArtifact"}


@dataclass
class Chunk:
    id: str
    repo_id: str
    revision: str
    path: str
    language: str
    symbol: str
    kind: str  # code | doc | config | text
    start_line: int
    end_line: int
    content_hash: str
    entity_ids: list[str] = field(default_factory=list)
    text: str = ""
    file_mtime: float = 0.0


def graph_id(e: Entity) -> str:
    return f"config:{e.name}" if e.type == "ConfigKey" else e.id


def _windows(lines: list[str], start: int, end: int, max_lines: int) -> list[tuple[int, int]]:
    """Split [start, end] (1-based, inclusive) into windows <= max_lines, preferring blank-line breaks."""
    out, cur = [], start
    while cur <= end:
        stop = min(end, cur + max_lines - 1)
        if stop < end:
            lo = cur + int(max_lines * 0.7)
            for j in range(stop, lo, -1):
                if not lines[j - 1].strip():
                    stop = j
                    break
        out.append((cur, stop))
        cur = stop + 1
    return out


def _nonblank(lines: list[str], a: int, b: int) -> bool:
    return any(lines[i - 1].strip() for i in range(a, b + 1))


def _cover(lines: list[str], units: list[tuple[int, int, str]], max_lines: int, gap_symbol: str) -> list[tuple[int, int, str]]:
    """Fill gaps between units so that every non-blank line belongs to exactly one range; enforce size limit."""
    n = len(lines)
    ranges: list[tuple[int, int, str]] = []
    cur = 1
    for a, b, sym in sorted(units):
        if a < cur:  # overlap: clip
            a = cur
        if a > b:
            continue
        if a > cur and _nonblank(lines, cur, a - 1):
            ranges += [(x, y, gap_symbol) for x, y in _windows(lines, cur, a - 1, max_lines)]
        parts = _windows(lines, a, b, max_lines)
        for i, (x, y) in enumerate(parts):
            ranges.append((x, y, sym if len(parts) == 1 else f"{sym} (part {i + 1}/{len(parts)})"))
        cur = b + 1
    if cur <= n and _nonblank(lines, cur, n):
        ranges += [(x, y, gap_symbol) for x, y in _windows(lines, cur, n, max_lines)]
    return [(a, b, s) for a, b, s in ranges if _nonblank(lines, a, b)]


def _code_units(parse: ParseResult, max_lines: int) -> list[tuple[int, int, str]]:
    ents = [e for e in parse.entities if e.type not in _SKIP_TYPES]
    children: dict[str | None, list[Entity]] = {}
    ids = {e.id for e in ents}
    for e in ents:
        key = e.parent_id if e.parent_id in ids else None
        children.setdefault(key, []).append(e)

    def emit(e: Entity) -> list[tuple[int, int, str]]:
        kids = sorted(children.get(e.id, []), key=lambda k: k.start_line)
        if e.end_line - e.start_line + 1 <= max_lines or not kids:
            return [(e.start_line, e.end_line, e.qualified_name)]
        out = []
        if kids[0].start_line > e.start_line:
            out.append((e.start_line, kids[0].start_line - 1, e.qualified_name))
        for k in kids:
            out += emit(k)
        last_end = max(k.end_line for k in kids)
        if last_end < e.end_line:
            out.append((last_end + 1, e.end_line, e.qualified_name + " (tail)"))
        return out

    units: list[tuple[int, int, str]] = []
    for e in sorted(children.get(None, []), key=lambda k: k.start_line):
        units += emit(e)
    return units


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _doc_units(lines: list[str]) -> list[tuple[int, int, str]]:
    heads = []
    in_code = False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(ln)
        if m:
            heads.append((i, m.group(2).strip()))
    if not heads:
        return []
    units = []
    for idx, (ln, title) in enumerate(heads):
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(lines)
        units.append((ln, end, title))
    return units


_YAML_KEY = re.compile(r"^[A-Za-z0-9_\"'][^:#]*:\s*(?:$|[^\s].*$)")
_SECTION = re.compile(r"^\s*\[[^\]]+\]\s*$")


def _config_units(lines: list[str], language: str) -> list[tuple[int, int, str]]:
    starts: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, 1):
        if language == "yaml" and _YAML_KEY.match(ln):
            starts.append((i, ln.split(":")[0].strip()))
        elif language in {"toml", "ini"} and _SECTION.match(ln):
            starts.append((i, ln.strip().strip("[]")))
        elif language == "dockerfile" and ln.upper().startswith("FROM "):
            starts.append((i, ln.strip()[:60]))
    units = []
    for idx, (ln, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        units.append((ln, end, name))
    # merge tiny neighbouring blocks so chunks stay meaningful
    merged: list[tuple[int, int, str]] = []
    for a, b, s in units:
        if merged and (merged[-1][1] - merged[-1][0] + 1) < 12 and (b - merged[-1][0] + 1) <= 60:
            pa, _, ps = merged.pop()
            merged.append((pa, b, f"{ps}, {s}"))
        else:
            merged.append((a, b, s))
    return merged


def chunk_file(repo_id: str, revision: str, path: str, language: str, text: str, parse: ParseResult | None,
               max_lines: int = 120, mtime: float = 0.0) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    if language in DOC_LANGS:
        kind, units, gap = "doc", _doc_units(lines), "<preamble>"
    elif language in CONFIG_LANGS:
        kind, units, gap = "config", _config_units(lines, language), "<config>"
    elif language in CODE_LANGS and parse is not None and parse.status in {"parsed", "partial", "syntax_error"}:
        kind, units, gap = "code", _code_units(parse, max_lines), "<module>"
    else:
        kind, units, gap = "text", [], "<text>"
    ranges = _cover(lines, units, max_lines, gap)
    ents = parse.entities if parse else []
    mod = next((e for e in ents if e.type == "Module"), None)
    chunks = []
    for a, b, sym in ranges:
        body = "\n".join(lines[a - 1:b])
        inside = [e for e in ents if e.type != "Module" and e.start_line >= a and e.end_line <= b]
        enclosing = [e for e in ents if e.type != "Module" and e.start_line <= a and e.end_line >= b
                     and not (e.start_line == a and e.end_line == b)]
        enclosing.sort(key=lambda e: e.end_line - e.start_line)
        eids = [graph_id(e) for e in inside]
        if enclosing:
            eids.append(graph_id(enclosing[0]))
        if not eids and mod is not None:
            eids.append(mod.id)
        eids = list(dict.fromkeys(eids))
        ch_hash = content_hash(body)
        cid = hashlib.sha1(f"{repo_id}|{path}|{a}|{b}|{ch_hash}".encode()).hexdigest()[:16]
        chunks.append(Chunk(cid, repo_id, revision, path, language, sym, kind, a, b, ch_hash, eids, body, mtime))
    return chunks
