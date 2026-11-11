"""Test planner: evidence-linked test specifications derived from real AST facts (no test code generated)."""
from __future__ import annotations

import ast

from agents.common import AgentContext, AgentOutput, claims_from_llm
from graph.dependency_analyzer import resolve_target
from verification.evidence_validator import Claim

_NUM = {"int", "float"}


def _boundaries(params: list[dict]) -> list[str]:
    out = []
    for p in params:
        a = (p.get("annotation") or "").replace(" ", "")
        n = p["name"]
        if a in _NUM or (p.get("default") or "").lstrip("-").replace(".", "").isdigit():
            out += [f"`{n}`: 0, negative value, very large value"]
        elif a.startswith("str") or (p.get("default") or "").startswith(("'", '"')):
            out += [f"`{n}`: empty string, whitespace only, very long string"]
        elif a.startswith(("list", "List", "set", "dict", "Dict", "tuple")):
            out += [f"`{n}`: empty collection, single element, duplicates"]
        if p.get("default") == "None" or "Optional" in a or "None" in a:
            out += [f"`{n}`: None"]
    return out


def _branch_cases(source: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    cases = []
    for n in ast.walk(tree):
        if isinstance(n, ast.If):
            first = n.body[0] if n.body else None
            if isinstance(first, ast.Return):
                exp = f"returns `{ast.unparse(first.value) if first.value else 'None'}`"
            elif isinstance(first, ast.Raise):
                exp = f"raises `{ast.unparse(first.exc) if first.exc else 'the active exception'}`"
            else:
                exp = None
            cases.append({"condition": ast.unparse(n.test), "line": n.lineno, "end": n.end_lineno or n.lineno, "expected": exp})
    return cases[:6]


def run(ctx: AgentContext, question: str, task: str = "tests", options: dict | None = None) -> AgentOutput:
    options = options or {}
    out = AgentOutput()
    g = ctx.graph()
    target = (options.get("target") or "").strip()
    if target:
        res = resolve_target(g, target)
        if res["kind"] in {"unknown", "ambiguous"}:
            out.insufficient = True
            out.payload["candidates"] = res["candidates"]
            out.summary = f"Target '{target}' was not found or is ambiguous; no test plan produced."
            return out
        ids = res["ids"]
    else:
        ids = [n for n, d in g.nodes(data=True) if d.get("type") in {"Function", "Method"} and d.get("language") == "python"]
    funcs = [g.nodes[n] | {"_id": n} for n in ids if g.nodes[n].get("type") in {"Function", "Method"}
             and not g.nodes[n]["name"].startswith("_") and g.nodes[n].get("language") == "python"]
    funcs.sort(key=lambda d: (d["path"], d["start_line"]))
    limit = int(options.get("max_functions", 15))
    specs = []
    for d in funcs[:limit]:
        attrs = d.get("attrs", {})
        src = "\n".join(ctx.chunks.file_lines(ctx.repo_id, d["path"], d["start_line"], d["end_line"]))
        import textwrap
        src_dedent = textwrap.dedent(src)
        ev = ctx.span(d["path"], d["start_line"], d["end_line"], d["qualified_name"], ("graph", "ast"))
        existing = [g.nodes[u]["qualified_name"] for u, _, e in g.in_edges(d["_id"], data=True) if e["type"] == "TESTS"]
        params = attrs.get("params", [])
        base = {"target": d["qualified_name"], "file": d["path"], "evidence_ids": [ev] if ev else [],
                "existing_tests": existing}
        doc = attrs.get("docstring") or ""
        specs.append({**base, "id": f"T{len(specs) + 1}", "category": "behavior",
                      "behavior": f"`{d['qualified_name']}` {('- ' + doc) if doc else 'normal operation'}",
                      "inputs_fixtures": [f"`{p['name']}`" + (f" (default {p['default']})" if p["has_default"] else "") for p in params] or ["no parameters"],
                      "expected_result": f"Return annotation: `{attrs['returns']}`." if attrs.get("returns") else "Not derivable from code",
                      "expected_basis": "partly derived from the signature; reviewer must confirm the exact expected value",
                      "boundary_conditions": _boundaries(params), "exception_scenarios": [],
                      "rationale": "Establish the documented/primary behavior before edge cases."})
        for exc in attrs.get("raises", []):
            specs.append({**base, "id": f"T{len(specs) + 1}", "category": "exception",
                          "behavior": f"`{d['qualified_name']}` raises `{exc}`", "inputs_fixtures": ["input that triggers the raise statement (see evidence)"],
                          "expected_result": f"raises `{exc}`", "expected_basis": "derived from a `raise` statement in the source",
                          "boundary_conditions": [], "exception_scenarios": [exc],
                          "rationale": "Each explicit raise is a contract worth testing."})
        for c in _branch_cases(src_dedent):
            line = d["start_line"] + c["line"] - 1
            bev = ctx.span(d["path"], line, d["start_line"] + c["end"] - 1, d["qualified_name"], ("ast",))
            specs.append({**base, "evidence_ids": [bev] if bev else base["evidence_ids"], "id": f"T{len(specs) + 1}",
                          "category": "branch", "behavior": f"`{d['qualified_name']}` when `{c['condition']}` is true and when it is false",
                          "inputs_fixtures": [f"one input satisfying `{c['condition']}`, one that does not"],
                          "expected_result": c["expected"] or "Not derivable from code",
                          "expected_basis": "derived from the first statement of the branch" if c["expected"] else "needs human input",
                          "boundary_conditions": [], "exception_scenarios": [], "rationale": f"Cover both outcomes of the branch at line {line}."})
    for s in specs:
        for e in s["evidence_ids"]:
            pass
        out.claims.append(Claim(f"Proposed {s['category']} test {s['id']} targets `{s['target']}`: {s['expected_result']}",
                                "recommendation", s["evidence_ids"]))
        s["untested"] = not s["existing_tests"]
    out.payload["tests"] = specs
    out.payload["coverage_note"] = ("'existing_tests' lists tests linked by a static call/naming heuristic; coverage was not measured. "
                                    "Test code is not generated and nothing is executed.")
    omitted = len(funcs) - limit
    if omitted > 0:
        out.notes.append(f"{omitted} further public functions were omitted (max_functions={limit}).")
    if not specs:
        out.insufficient = True
        out.summary = "No testable Python functions were found for this target."
        return out
    bundle = ctx.search((target or question) + " test", "tests", k=6)
    data = ctx.llm_json("Suggest additional test cases worth adding.",
                        "Only propose tests grounded in the evidence; give expected behaviour only if the code states it.",
                        '{"claims": [{"text": str, "kind": "recommendation", "evidence": ["E1"]}]}', question, bundle.items)
    if data is not None:
        ai = claims_from_llm(data, ctx)
        out.payload["ai_proposed"] = [c.text for c in ai]
        out.claims += ai
        out.mode = "llm+deterministic"
    else:
        out.notes.append("AI refinement skipped: " + (ctx.llm_error or "no local model configured"))
    out.summary = f"{len(specs)} proposed test case(s) for {min(len(funcs), limit)} function(s); all derived from AST facts with evidence."
    return out


def to_markdown(plan: dict) -> str:
    lines = ["# Test plan (proposal - nothing executed)", ""]
    for t in plan.get("tests", []):
        lines += [f"## {t['id']} [{t['category']}] {t['target']}", f"- **Behavior:** {t['behavior']}",
                  f"- **Evidence:** {', '.join(t['evidence_ids']) or 'none'}", f"- **Inputs/fixtures:** {'; '.join(t['inputs_fixtures'])}",
                  f"- **Expected result:** {t['expected_result']} _({t['expected_basis']})_"]
        if t["boundary_conditions"]:
            lines.append(f"- **Boundaries:** {'; '.join(t['boundary_conditions'])}")
        if t["exception_scenarios"]:
            lines.append(f"- **Exceptions:** {', '.join(t['exception_scenarios'])}")
        lines += [f"- **Existing tests:** {', '.join(t['existing_tests']) or 'none found'}", f"- **Rationale:** {t['rationale']}", ""]
    return "\n".join(lines)
