"""Tree-sitter adapter (optional). Used when `tree_sitter` and a grammar package are installed.

If a grammar is missing, `available()` returns False and callers use the regex fallback, which is
labelled heuristic. NOTE: this adapter is exercised by tests only when the grammars are installed.
"""
from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import PurePosixPath

from parsing.entity_extractor import Entity, ParseResult, Relation, content_hash, make_entity_id

_GRAMMARS = {  # language -> (module, function returning the language pointer)
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "java": ("tree_sitter_java", "language"),
    "go": ("tree_sitter_go", "language"),
}
SUPPORTED = set(_GRAMMARS)

_DEFS = {  # node type -> entity type
    "javascript": {"function_declaration": "Function", "class_declaration": "Class", "method_definition": "Method"},
    "typescript": {"function_declaration": "Function", "class_declaration": "Class", "method_definition": "Method",
                   "interface_declaration": "Class"},
    "java": {"class_declaration": "Class", "interface_declaration": "Class", "enum_declaration": "Class",
             "method_declaration": "Method", "constructor_declaration": "Method"},
    "go": {"function_declaration": "Function", "method_declaration": "Method", "type_spec": "Class"},
}
_IMPORT_NODES = {"javascript": {"import_statement"}, "typescript": {"import_statement"},
                 "java": {"import_declaration"}, "go": {"import_spec"}}
_CALL_NODES = {"javascript": {"call_expression"}, "typescript": {"call_expression"},
               "java": {"method_invocation"}, "go": {"call_expression"}}
_METHOD = "tree-sitter"


@lru_cache(maxsize=None)
def _load(language: str):
    try:
        from tree_sitter import Language, Parser
        mod_name, fn = _GRAMMARS[language]
        mod = importlib.import_module(mod_name)
        return Parser(Language(getattr(mod, fn)()))
    except Exception:
        return None


def available(language: str) -> bool:
    return language in _GRAMMARS and _load(language) is not None


def _text(node) -> str:
    return node.text.decode("utf-8", "replace") if node.text else ""


def parse_with_treesitter(path: str, text: str, language: str) -> ParseResult | None:
    parser = _load(language)
    if parser is None:
        return None
    src = text.encode("utf-8")
    tree = parser.parse(src)
    lines = text.splitlines()
    mod_qn = PurePosixPath(path).with_suffix("").as_posix().replace("/", ".")
    mod = Entity(make_entity_id(path, mod_qn, "Module"), "Module", PurePosixPath(path).stem, mod_qn, path, 1,
                 max(len(lines), 1), language, content_hash(text), f"{_METHOD}:{language}", "exact", None, {})
    res = ParseResult(path, language, f"{_METHOD}:{language}", "parsed", mod_qn, [mod], [], {}, [], len(lines))
    if tree.root_node.has_error:
        res.status = "partial"
        res.errors.append("tree-sitter reported syntax errors; extraction may be incomplete")
    defs, imports, calls = _DEFS[language], _IMPORT_NODES[language], _CALL_NODES[language]

    def visit(node, parent: Entity, scope: str):
        cur, cur_scope = parent, scope
        if node.type in defs:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node)
                qn = f"{scope}.{name}"
                s, e = node.start_point[0] + 1, node.end_point[0] + 1
                ent = Entity(make_entity_id(path, f"{qn}@{s}", defs[node.type]), defs[node.type], name, qn, path, s, e,
                             language, content_hash("\n".join(lines[s - 1:e])), f"{_METHOD}:{language}", "exact",
                             parent.id, {})
                res.entities.append(ent)
                cur, cur_scope = ent, qn
                for child in node.children:
                    if child.type in {"class_heritage", "superclass", "extends_clause"}:
                        for tok in _text(child).replace("extends", " ").replace(",", " ").split():
                            res.relations.append(Relation(ent.id, "INHERITS", tok.strip("{"), "name", _METHOD, path,
                                                          s, "heuristic"))
                    if child.type in {"super_interfaces", "implements_clause"}:
                        for tok in _text(child).replace("implements", " ").replace(",", " ").split():
                            res.relations.append(Relation(ent.id, "IMPLEMENTS", tok.strip("{"), "name", _METHOD, path,
                                                          s, "heuristic"))
        elif node.type in imports:
            spec = node.child_by_field_name("source") or node.child_by_field_name("path")
            raw = _text(spec) if spec is not None else _text(node)
            raw = raw.strip("\"'` ;").replace("import ", "").strip()
            res.relations.append(Relation(mod.id, "IMPORTS", raw, "module", f"{_METHOD}:{language}", path,
                                          node.start_point[0] + 1, "exact", {"names": [], "relative": raw.startswith(".")}))
        elif node.type in calls:
            fn = node.child_by_field_name("function") or node.child_by_field_name("name")
            if fn is not None:
                obj = node.child_by_field_name("object")
                raw = (_text(obj) + "." if obj is not None else "") + _text(fn)
                res.relations.append(Relation(parent.id, "CALLS", raw.replace("\n", "")[:120], "name",
                                              f"{_METHOD}:{language}", path, node.start_point[0] + 1, "heuristic"))
        for child in node.children:
            visit(child, cur, cur_scope)

    visit(tree.root_node, mod, mod_qn)
    return res
