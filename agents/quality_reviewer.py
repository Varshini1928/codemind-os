"""Quality reviewer: deterministic AST rules (Python) plus optional, clearly separated AI suggestions."""
from __future__ import annotations

import ast
import re

from agents.common import AgentContext, AgentOutput, claims_from_llm
from verification.evidence_validator import Claim
from verification.safety_policy import find_secrets

SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_CRED_NAME = re.compile(r"(?i)(password|passwd|secret|api_?key|token)")
_PLACEHOLDER = re.compile(r"(?i)^(changeme|change_me|password|secret|xxx+|\*+|<.*>|\$\{.*\}|your[_-].*|example|test|dummy)$")


def _name(n):
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    return ".".join(reversed(parts))


def scan_python(path: str, text: str, is_test: bool) -> list[dict]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    f: list[dict] = []

    def add(rule, node, title, sev, cat, why, unc, nxt):
        f.append({"rule": rule, "path": path, "line": node.lineno, "end": getattr(node, "end_lineno", node.lineno),
                  "title": title, "severity": sev, "category": cat, "explanation": why, "uncertainty": unc,
                  "next_step": nxt})

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add("bare-except", node, "Bare `except:` clause", "medium", "reliability",
                    "Catches every exception including KeyboardInterrupt/SystemExit and can hide bugs.",
                    "May be intentional at a process boundary.", "Catch specific exceptions and log or re-raise.")
            elif _name(node.type) in {"Exception", "BaseException"} and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                add("swallowed-exception", node, "Exception swallowed with `pass`", "medium", "reliability",
                    "Errors are silently ignored, which makes failures hard to diagnose.",
                    "The error may be genuinely ignorable.", "Log the exception or narrow the handler.")
        elif isinstance(node, ast.Call):
            fn = _name(node.func)
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            if fn in {"eval", "exec"}:
                add("eval-exec", node, f"Use of `{fn}`", "high", "security",
                    f"`{fn}` executes dynamically built code; untrusted input would lead to code execution.",
                    "Safe only if the argument is fully trusted; data flow was not analysed.", "Replace with ast.literal_eval or explicit parsing.")
            elif fn.startswith("subprocess.") and isinstance(kw.get("shell"), ast.Constant) and kw["shell"].value is True:
                add("shell-true", node, "subprocess call with `shell=True`", "high", "security",
                    "Shell invocation enables command injection when any part of the command is user-controlled.",
                    "Whether the command string includes untrusted data was not analysed.", "Pass an argument list and drop shell=True.")
            elif fn == "os.system":
                add("os-system", node, "Use of `os.system`", "medium", "security",
                    "Runs a shell command; vulnerable to injection if the string includes untrusted input.",
                    "Data flow not analysed.", "Use subprocess.run with an argument list.")
            elif fn in {"pickle.load", "pickle.loads", "marshal.loads"}:
                add("unsafe-deserialization", node, f"Unsafe deserialization via `{fn}`", "medium", "security",
                    "Unpickling untrusted data can execute arbitrary code.", "Source of the data was not analysed.",
                    "Use a safe format (JSON) for untrusted data.")
            elif fn == "yaml.load" and "Loader" not in kw and len(node.args) < 2:
                add("yaml-load", node, "`yaml.load` without an explicit Loader", "medium", "security",
                    "Unsafe loaders can construct arbitrary objects.", "Depends on the installed PyYAML version.", "Use yaml.safe_load.")
            elif fn.startswith("requests.") and isinstance(kw.get("verify"), ast.Constant) and kw["verify"].value is False:
                add("tls-verify-off", node, "TLS verification disabled", "medium", "security",
                    "`verify=False` exposes the connection to interception.", "May be limited to a test environment.", "Remove verify=False or pin a CA bundle.")
            elif fn.startswith("requests.") and fn.split(".")[-1] in {"get", "post", "put", "delete", "patch", "head"} and "timeout" not in kw:
                add("no-timeout", node, f"`{fn}` without timeout", "low", "reliability",
                    "A missing timeout can block a worker indefinitely.", "Session-level defaults were not analysed.", "Pass an explicit timeout.")
            elif fn.split(".")[-1] in {"execute", "executemany"} and node.args:
                a = node.args[0]
                dyn = isinstance(a, ast.JoinedStr) or (isinstance(a, ast.BinOp) and isinstance(a.op, (ast.Mod, ast.Add))) \
                    or (isinstance(a, ast.Call) and _name(a.func).endswith(".format"))
                if dyn:
                    add("sql-string-building", node, "SQL built with string formatting", "high", "security",
                        "Interpolating values into SQL can allow SQL injection.", "Whether the values are user-controlled was not analysed.",
                        "Use parameterised queries.")
            elif fn.endswith(".run") and isinstance(kw.get("debug"), ast.Constant) and kw["debug"].value is True:
                add("debug-enabled", node, "Debug mode enabled", "medium", "security",
                    "Debug servers can expose interactive consoles and stack traces.", "May only run in development.", "Disable debug outside local development.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x is not None]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)) or (isinstance(d, ast.Call) and _name(d.func) in {"list", "dict", "set"}):
                    add("mutable-default", d, "Mutable default argument", "medium", "correctness",
                        "The default object is shared between calls and can leak state.", "Only a problem if it is mutated.",
                        "Use None and create the object inside the function.")
            n = (node.end_lineno or node.lineno) - node.lineno + 1
            if n > 80 and not is_test:
                add("long-function", node, f"Long function `{node.name}` ({n} lines)", "low", "maintainability",
                    "Long functions are harder to test and review.", "Length alone is not a defect.", "Consider extracting cohesive helpers.")
        elif isinstance(node, ast.Assert) and not is_test:
            add("assert-in-production", node, "`assert` used in non-test code", "low", "correctness",
                "Asserts are removed with `python -O`, so the check may disappear.", "May be a developer-only invariant.",
                "Raise an explicit exception for runtime validation.")
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                nm = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else "")
                v = node.value.value
                if nm and _CRED_NAME.search(nm) and len(v) >= 4 and not _PLACEHOLDER.match(v):
                    add("hardcoded-credential", node, f"Hard-coded credential-like value in `{nm}`", "medium", "security",
                        "Credentials committed to source control are exposed to everyone with repository access (value redacted).",
                        "Could be a placeholder, test value or non-secret.", "Load secrets from the environment or a secret store.")
    return f


def run(ctx: AgentContext, question: str, task: str = "quality", options: dict | None = None) -> AgentOutput:
    options = options or {}
    out = AgentOutput()
    paths = [p for p in ctx.chunks.paths(ctx.repo_id)]
    if options.get("paths"):
        wanted = set(options["paths"])
        paths = [p for p in paths if p in wanted or any(p.startswith(w.rstrip("/") + "/") for w in wanted)]
    py = [p for p in paths if p.endswith(".py")][: int(options.get("max_files", 400))]
    findings = []
    for p in py:
        text = ctx.chunks.file_text(ctx.repo_id, p)
        is_test = "/tests/" in "/" + p or p.rsplit("/", 1)[-1].startswith("test_")
        for f in scan_python(p, text, is_test):
            f["evidence_id"] = ctx.span(p, f["line"], f["end"], methods=("static-rule",))
            f["method"] = f"ast-rule:{f.pop('rule')}"
            f["confidence"] = "candidate (rule match; not a confirmed defect)"
            findings.append(f)
    # TODO/FIXME markers (text heuristic)
    for p in paths:
        if p.endswith((".py", ".js", ".ts", ".java", ".go")):
            for i, ln in enumerate(ctx.chunks.file_lines(ctx.repo_id, p), 1):
                if re.search(r"#\s*(TODO|FIXME|HACK|XXX)\b|//\s*(TODO|FIXME|HACK)\b", ln):
                    findings.append({"path": p, "line": i, "end": i, "title": "Open TODO/FIXME marker", "severity": "info",
                                     "category": "maintainability", "explanation": ln.strip()[:160],
                                     "uncertainty": "Text match; may sit inside a string.", "next_step": "Track or resolve it.",
                                     "evidence_id": ctx.span(p, i, i, methods=("text-match",)), "method": "text-match:todo",
                                     "confidence": "candidate"})
    for p in paths:  # password-like strings in non-Python files (redacted)
        if not p.endswith(".py") and p.rsplit(".", 1)[-1] in {"js", "ts", "java", "go", "yaml", "yml", "json", "ini", "toml"}:
            for h in find_secrets(ctx.chunks.file_text(ctx.repo_id, p)):
                findings.append({"path": p, "line": h.line, "end": h.line, "title": "Credential-like assignment", "severity": "medium",
                                 "category": "security", "explanation": "A password/secret-like assignment was found (value redacted).",
                                 "uncertainty": "Pattern match; may be a placeholder.", "next_step": "Move secrets out of the repository.",
                                 "evidence_id": ctx.span(p, h.line, h.line, methods=("pattern",)), "method": "pattern:credential",
                                 "confidence": "candidate"})
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["path"], f["line"]))
    for n, f in enumerate(findings, 1):
        f["id"] = f"F{n}"
        f["evidence_ids"] = [f.pop("evidence_id")] if f.get("evidence_id") else []
        out.claims.append(Claim(f"[{f['severity'].upper()}] {f['title']} at `{f['path']}` line {f['line']}: {f['explanation']}",
                                "fact", f["evidence_ids"]))
    out.payload["findings"] = findings
    out.payload["scope"] = {"files_in_index": len(paths), "python_files_scanned": len(py),
                            "not_scanned": "Rule-based checks cover Python only; other languages are listed as not reviewed."}
    out.payload["severity_counts"] = {s: sum(1 for f in findings if f["severity"] == s) for s in SEV_ORDER}
    # ---- optional AI review (kept separate and labelled)
    bundle = ctx.search(question if question.strip() else "error handling validation security", "quality", k=8)
    data = ctx.llm_json("Review these code excerpts for candidate correctness, reliability, security or maintainability concerns.",
                        "Report only concerns visible in the evidence. State severity (high|medium|low), uncertainty and a next step.",
                        '{"claims": [{"text": str, "kind": "interpretation|recommendation", "evidence": ["E1"]}], "unknowns": [str]}',
                        question, bundle.items)
    if data is not None:
        ai = claims_from_llm(data, ctx)
        for c in ai:
            c.kind = "interpretation" if c.kind == "fact" else c.kind
            c.notes.append("AI-suggested concern: unverified interpretation, not a confirmed defect")
        out.claims += ai
        out.payload["ai_suggestions"] = [c.text for c in ai]
        out.mode = "llm+deterministic"
    else:
        out.notes.append("AI review skipped: " + (ctx.llm_error or "no local model configured"))
    out.summary = (f"{len(findings)} candidate finding(s): " + ", ".join(f"{v} {k}" for k, v in out.payload["severity_counts"].items() if v)
                   if findings else "No rule-based findings in the scanned files (this does not mean the code is free of defects).")
    return out
