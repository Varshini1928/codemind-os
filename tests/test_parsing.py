import pytest

from parsing import treesitter_parser
from parsing.entity_extractor import parse_file

SRC = '''import os
from .util import helper as h

class A(Base):
    """Doc."""
    def m(self, x: int = 0):
        if x:
            raise ValueError("bad")
        return self.n(x) + h(x) + len(os.getenv("KEY", "d"))

    def n(self, x):
        return x


@app.get("/items")
async def items():
    return A().m()


if __name__ == "__main__":
    items()
'''


def test_python_extraction():
    r = parse_file("pkg/mod.py", SRC)
    assert r.status == "parsed" and r.parser == "python-ast"
    by = {e.qualified_name: e for e in r.entities}
    assert by["pkg.mod"].type == "Module"
    assert by["pkg.mod.A"].type == "DatabaseEntity" and by["pkg.mod.A"].confidence == "heuristic"
    assert by["pkg.mod.A.m"].type == "Method" and by["pkg.mod.A.m"].start_line == 6
    assert by["pkg.mod.items"].attrs["async"] is True
    for e in r.entities:
        assert e.id and e.content_hash and e.extraction_method and e.language == "python" and e.start_line <= e.end_line
    kinds = {(x.type, x.dst) for x in r.relations}
    assert ("IMPORTS", "pkg.util") in kinds and ("IMPORTS", "os") in kinds
    assert ("READS_CONFIG", "KEY") in kinds and ("INHERITS", "Base") in kinds
    assert ("CALLS", "self.n") in kinds
    assert r.has_main_guard and r.import_map["h"] == "pkg.util.helper"
    assert any(e.type == "Endpoint" and e.attrs["route"] == "/items" for e in r.entities)


def test_stable_ids():
    assert [e.id for e in parse_file("a.py", SRC).entities] == [e.id for e in parse_file("a.py", SRC).entities]


def test_syntax_error_does_not_abort():
    r = parse_file("bad.py", "def f(:\n    pass\n\ndef ok():\n    return 1\n")
    assert r.status == "syntax_error" and r.errors
    assert any(e.name == "ok" and e.confidence == "heuristic" for e in r.entities)


def test_unsupported_language_fallback_is_heuristic():
    r = parse_file("x.rs", "pub fn hello() {\n    println!(\"hi\");\n}\n")
    assert r.parser == "regex-heuristic" and r.status == "partial"
    assert r.entities[1].name == "hello" and r.entities[1].confidence == "heuristic"
    r2 = parse_file("notes.txt", "just words\n")
    assert r2.status == "text_only"


def test_js_fallback_without_treesitter():
    src = "import x from './b';\nexport function foo(a) {\n  return 1;\n}\nclass K {}\n"
    r = parse_file("a.js", src)
    names = {e.name: e for e in r.entities}
    assert names["foo"].end_line == 4
    if treesitter_parser.available("javascript"):
        assert r.parser.startswith("tree-sitter") and names["foo"].confidence == "exact"
    else:
        assert r.parser == "regex-heuristic" and names["foo"].confidence == "heuristic"


@pytest.mark.skipif(not treesitter_parser.available("javascript"), reason="tree-sitter-javascript not installed")
def test_treesitter_javascript():
    r = parse_file("a.js", "function foo(){ bar(); }\nfunction bar(){}\n")
    assert r.parser == "tree-sitter:javascript"
    assert any(x.type == "CALLS" and x.dst == "bar" for x in r.relations)


def test_deployment_and_env_parsing():
    d = parse_file("Dockerfile", 'FROM python:3.11\nCMD ["python", "main.py"]\n')
    assert any(e.type == "DeploymentArtifact" for e in d.entities) and d.relations[0].dst == "main.py"
    e = parse_file(".env.example", "A=1\nB=2\n")
    assert [x.name for x in e.entities if x.type == "ConfigKey"] == ["A", "B"]
