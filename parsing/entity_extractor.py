"""Entity/relation data model and the parser dispatcher."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from parsing.languages import detect_language


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def make_entity_id(path: str, qualified_name: str, etype: str) -> str:
    return "e_" + hashlib.sha1(f"{path}::{qualified_name}::{etype}".encode()).hexdigest()[:16]


@dataclass
class Entity:
    id: str
    type: str  # Module Class Function Method Test Endpoint DatabaseEntity ConfigKey DeploymentArtifact
    name: str
    qualified_name: str
    path: str
    start_line: int
    end_line: int
    language: str
    content_hash: str
    extraction_method: str
    confidence: str  # "exact" (syntax-tree derived) or "heuristic"
    parent_id: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Relation:
    src: str  # entity id
    type: str  # CALLS IMPORTS INHERITS IMPLEMENTS READS_CONFIG EXPOSES DEPLOYS
    dst: str  # raw name / dotted module / entity id / path (see dst_kind)
    dst_kind: str = "name"  # name | module | entity_id | config | path
    method: str = ""
    path: str = ""
    line: int = 0
    confidence: str = "heuristic"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    path: str
    language: str
    parser: str
    status: str  # parsed | partial | syntax_error | text_only | failed
    module_name: str = ""
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    import_map: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    line_count: int = 0
    has_main_guard: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ParseResult":
        d = dict(d)
        d["entities"] = [Entity(**e) for e in d.get("entities", [])]
        d["relations"] = [Relation(**r) for r in d.get("relations", [])]
        return cls(**d)


def parse_file(path: str, text: str, language: str | None = None) -> ParseResult:
    """Dispatch to the best available parser. Never raises: failures become ParseResult.status."""
    from parsing import python_parser, text_fallback, treesitter_parser
    language = language or detect_language(path)
    try:
        if language == "python":
            return python_parser.parse_python(path, text)
        if language in treesitter_parser.SUPPORTED and treesitter_parser.available(language):
            res = treesitter_parser.parse_with_treesitter(path, text, language)
            if res is not None:
                return res
        return text_fallback.parse_generic(path, text, language)
    except Exception as e:  # defensive: a parser bug must not abort the whole indexing job
        return ParseResult(path, language, "none", "failed", errors=[f"{type(e).__name__}: {e}"[:300]],
                           line_count=text.count("\n") + 1)
