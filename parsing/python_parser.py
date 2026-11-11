"""Python analysis with the standard-library ast module (exact extraction)."""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from parsing.entity_extractor import Entity, ParseResult, Relation, content_hash, make_entity_id

_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "route", "websocket"}
_DB_BASES = {"Base", "DeclarativeBase", "Model", "SQLModel"}
_METHOD = "python-ast"


def module_name_from_path(path: str) -> tuple[str, bool]:
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    is_pkg = p.name == "__init__.py"
    if is_pkg:
        parts = parts[:-1]
    return (".".join(parts) or p.stem), is_pkg


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if parts:  # chain rooted in a call/subscript: only the tail is statically known
        return "<dynamic>." + ".".join(reversed(parts))
    return None


def _is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts or "test" in p.parts


def _doc(node) -> str:
    d = ast.get_docstring(node) or ""
    return d.strip().split("\n\n")[0].replace("\n", " ")[:240]


def _iter_own_nodes(node: ast.AST):
    """Yield nodes inside `node` without descending into nested function/class definitions."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _env_key(call: ast.Call) -> str | None:
    f = _dotted(call.func) or ""
    if f in {"os.getenv", "os.environ.get", "environ.get", "getenv"} and call.args:
        a = call.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
    return None


def _env_subscript(n: ast.Subscript) -> str | None:
    if (_dotted(n.value) or "") in {"os.environ", "environ"}:
        sl = n.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return None


class _Parser:
    def __init__(self, path: str, text: str, tree: ast.Module):
        self.path, self.text, self.tree = path, text, tree
        self.lines = text.splitlines()
        self.module_qn, self.is_pkg = module_name_from_path(path)
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []
        self.import_map: dict[str, str] = {}
        self.test_file = _is_test_path(path)

    def _src(self, a: int, b: int) -> str:
        return "\n".join(self.lines[a - 1:b])

    def _entity(self, etype, name, qn, node_start, node_end, parent, confidence="exact", method=_METHOD, attrs=None):
        e = Entity(make_entity_id(self.path, qn, etype), etype, name, qn, self.path, node_start, node_end,
                   "python", content_hash(self._src(node_start, node_end)), method, confidence,
                   parent.id if parent else None, attrs or {})
        self.entities.append(e)
        return e

    def _rel(self, src, rtype, dst, kind, line, conf="heuristic", method=_METHOD, **extra):
        self.relations.append(Relation(src.id, rtype, dst, kind, method, self.path, line, conf, extra))

    # ------------------------------------------------------------ body walking
    def run(self) -> ParseResult:
        n = len(self.lines)
        mod = self._entity("Module", self.module_qn.rsplit(".", 1)[-1], self.module_qn, 1, max(n, 1), None,
                           attrs={"docstring": _doc(self.tree), "is_package": self.is_pkg})
        self.module = mod
        has_main = False
        for stmt in self.tree.body:
            if isinstance(stmt, ast.If) and "__name__" in ast.unparse(stmt.test) and "__main__" in ast.unparse(stmt.test):
                has_main = True
                mod.attrs["main_line"] = [stmt.lineno, stmt.end_lineno or stmt.lineno]
        mod.attrs["entry_point"] = has_main
        self._imports()
        self._visit_body(self.tree.body, mod, self.module_qn, None)
        self._module_level_effects(mod)
        return ParseResult(self.path, "python", _METHOD, "parsed", self.module_qn, self.entities, self.relations,
                           self.import_map, [], n, has_main)

    def _imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    target = a.name
                    self.import_map[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                    self._rel(self.module, "IMPORTS", target, "module", node.lineno, "exact", names=[])
            elif isinstance(node, ast.ImportFrom):
                base = self._absolute(node.module, node.level)
                names = [a.name for a in node.names]
                for a in node.names:
                    if a.name != "*":
                        self.import_map[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
                self._rel(self.module, "IMPORTS", base, "module", node.lineno, "exact", names=names, level=node.level)

    def _absolute(self, module: str | None, level: int) -> str:
        if not level:
            return module or ""
        pkg = self.module_qn if self.is_pkg else self.module_qn.rpartition(".")[0]
        for _ in range(level - 1):
            pkg = pkg.rpartition(".")[0]
        return f"{pkg}.{module}" if module and pkg else (module or pkg)

    def _visit_body(self, body, parent, scope_qn, cls):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(stmt, parent, scope_qn, cls)
            elif isinstance(stmt, ast.ClassDef):
                self._class(stmt, parent, scope_qn)
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for attr in ("body", "orelse", "finalbody"):
                    self._visit_body(getattr(stmt, attr, []) or [], parent, scope_qn, cls)
                for h in getattr(stmt, "handlers", []) or []:
                    self._visit_body(h.body, parent, scope_qn, cls)

    def _class(self, node: ast.ClassDef, parent, scope_qn):
        qn = f"{scope_qn}.{node.name}"
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        bases = [b for b in (_dotted(x) for x in node.bases) if b]
        is_db = any(b.rsplit(".", 1)[-1] in _DB_BASES for b in bases)
        is_test = self.test_file and node.name.startswith("Test")
        etype = "DatabaseEntity" if is_db else ("Test" if is_test else "Class")
        e = self._entity(etype, node.name, qn, start, node.end_lineno or node.lineno, parent,
                         "heuristic" if is_db else "exact",
                         attrs={"docstring": _doc(node), "bases": bases,
                                "decorators": [ast.unparse(d) for d in node.decorator_list]})
        for b in bases:
            self._rel(e, "INHERITS", b, "name", node.lineno, "heuristic")
        self._visit_body(node.body, e, qn, e)

    def _function(self, node, parent, scope_qn, cls):
        qn = f"{scope_qn}.{node.name}"
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        end = node.end_lineno or node.lineno
        in_class = parent is not None and parent.type in {"Class", "DatabaseEntity", "Test"}
        is_test = self.test_file and node.name.startswith("test")
        etype = "Test" if is_test else ("Method" if in_class else "Function")
        args = node.args
        pos = list(args.posonlyargs) + list(args.args)
        defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
        params = [{"name": a.arg, "annotation": ast.unparse(a.annotation) if a.annotation else None,
                   "has_default": d is not None, "default": ast.unparse(d) if d is not None else None}
                  for a, d in zip(pos, defaults) if a.arg not in {"self", "cls"}]
        params += [{"name": a.arg, "annotation": ast.unparse(a.annotation) if a.annotation else None,
                    "has_default": d is not None, "default": ast.unparse(d) if d is not None else None}
                   for a, d in zip(args.kwonlyargs, args.kw_defaults)]
        own = list(_iter_own_nodes(node))
        branches = sum(isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp, ast.BoolOp, ast.ExceptHandler,
                                      ast.comprehension)) for n in own)
        raises = sorted({(_dotted(n.exc.func) if isinstance(n.exc, ast.Call) else _dotted(n.exc)) or "?"
                         for n in own if isinstance(n, ast.Raise) and n.exc is not None})
        attrs = {"docstring": _doc(node), "params": params, "async": isinstance(node, ast.AsyncFunctionDef),
                 "returns": ast.unparse(node.returns) if node.returns else None, "branches": branches,
                 "raises": raises, "decorators": [ast.unparse(d) for d in node.decorator_list],
                 "signature": f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({ast.unparse(args)})"}
        e = self._entity(etype, node.name, qn, start, end, parent, attrs=attrs)
        for n in own:
            if isinstance(n, ast.Call):
                key = _env_key(n)
                if key:
                    self._rel(e, "READS_CONFIG", key, "config", n.lineno, "exact", method="python-ast:env")
                d = _dotted(n.func)
                if d:
                    self._rel(e, "CALLS", d, "name", n.lineno, "heuristic")
            elif isinstance(n, ast.Subscript):
                key = _env_subscript(n)
                if key:
                    self._rel(e, "READS_CONFIG", key, "config", n.lineno, "exact", method="python-ast:env")
        for dec in node.decorator_list:
            self._endpoint(dec, node, e, parent)
        self._visit_body(node.body, e, qn, None)

    def _endpoint(self, dec, node, handler, parent):
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _HTTP_VERBS):
            return
        if not dec.args or not (isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
            return
        route, verb = dec.args[0].value, dec.func.attr.upper()
        if verb == "ROUTE":
            methods = next((k.value for k in dec.keywords if k.arg == "methods"), None)
            verb = ",".join(ast.literal_eval(methods)) if methods is not None and isinstance(methods, (ast.List, ast.Tuple)) else "ANY"
        owner = _dotted(dec.func.value) or "?"
        qn = f"endpoint:{verb}:{route}:{handler.qualified_name}"
        mod = next(x for x in self.entities if x.type == "Module")
        ep = self._entity("Endpoint", f"{verb} {route}", qn, handler.start_line, handler.end_line, mod,
                          "heuristic", "python-ast:decorator",
                          {"route": route, "http_method": verb, "router": owner, "handler": handler.qualified_name})
        self._rel(handler, "EXPOSES", ep.id, "entity_id", dec.lineno, "heuristic", method="python-ast:decorator")

    def _module_level_effects(self, mod):
        for stmt in self.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in [stmt, *_iter_own_nodes(stmt)]:
                if isinstance(n, ast.Call):
                    key = _env_key(n)
                    if key:
                        self._rel(mod, "READS_CONFIG", key, "config", n.lineno, "exact", method="python-ast:env")
                    d = _dotted(n.func)
                    if d:
                        self._rel(mod, "CALLS", d, "name", n.lineno, "heuristic")
                elif isinstance(n, ast.Subscript):
                    key = _env_subscript(n)
                    if key:
                        self._rel(mod, "READS_CONFIG", key, "config", n.lineno, "exact", method="python-ast:env")


def parse_python(path: str, text: str) -> ParseResult:
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError) as e:
        from parsing import text_fallback
        res = text_fallback.parse_generic(path, text, "python")
        msg = f"{type(e).__name__}: {getattr(e, 'msg', e)} (line {getattr(e, 'lineno', '?')})"
        res.status = "syntax_error"
        res.errors.append(msg)
        return res
    return _Parser(path, text, tree).run()
