"""Stateful LangGraph workflow: classify -> plan -> (route) specialist -> ... -> verify -> assemble.

If `langgraph` is not installed the same node functions run in a plain sequential engine; the result reports which
engine executed (`engine`).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, TypedDict

from agents import documentation_writer, impact_analyst, quality_reviewer, repository_analyst, test_planner
from agents.common import AgentContext, AgentOutput
from verification.citation_checker import CitationChecker
from verification.evidence_validator import verify_claims

TASKS = ["architecture", "question", "quality", "impact", "tests", "documentation"]
SPECIALISTS = {"architecture": repository_analyst, "question": repository_analyst, "quality": quality_reviewer,
               "impact": impact_analyst, "tests": test_planner, "documentation": documentation_writer}
NODE_OF = {"architecture": "analyst", "question": "analyst", "quality": "quality", "impact": "impact",
           "tests": "tests", "documentation": "docs"}
MAX_TASKS = 4

_RULES = [
    ("impact", r"\bimpact|affect(?:ed|s)?\b|\bbreak(?:s|ing)?\b|ripple|blast radius|who (?:calls|uses)|callers? of|depends? on|if i (?:change|rename|remove|modify)"),
    ("tests", r"test plan|write tests?|unit tests?|test cases?|how (?:do i|to|should i) test|what to test|coverage"),
    ("documentation", r"document(?:ation)?\b|readme|api reference|setup instructions|developer guide|generate docs"),
    ("quality", r"\bbugs?\b|vulnerab|security|code quality|code review|smells?|risks?\b|problems?|issues?\b|weakness"),
    ("architecture", r"architecture|high[- ]level|overview|how is .* (?:organi[sz]ed|structured)|entry ?points?|data flow|deployment|components"),
]


def classify(text: str) -> str:
    """Rule-based intent classifier (deterministic, works without an LLM)."""
    t = text.lower()
    for task, rx in _RULES:
        if re.search(rx, t):
            return task
    return "question"


def decompose(text: str) -> list[str]:
    parts = re.split(r"(?<=\?)\s+(?=[A-Z])|;\s+|\band also\b|\n\s*\d+[.)]\s+", text.strip())
    parts = [p.strip(" \n") for p in parts if p and len(p.strip()) >= 12]
    return parts[:MAX_TASKS] or [text.strip()]


class WorkflowState(TypedDict, total=False):
    user_request: str
    repository_id: str
    revision: str
    forced_task: str | None
    options: dict
    task: str
    tasks: list[dict]
    cursor: int
    subquestions: list[str]
    sections: list[dict]
    evidence: list[dict]
    graph_neighborhood: dict
    findings: list[dict]
    confidence: str
    uncertainty: list[str]
    citation_validation: dict
    proposed_artifacts: list[str]
    errors: list[str]
    status: str
    result: dict
    raw_outputs: list[Any]


class Workflow:
    def __init__(self, ctx: AgentContext, checker: CitationChecker, model_name: str = ""):
        self.ctx, self.checker, self.model = ctx, checker, model_name

    # ---------------------------------------------------------------- nodes
    def n_classify(self, st: WorkflowState) -> dict:
        task = st.get("forced_task") or classify(st["user_request"])
        return {"task": task, "errors": [], "uncertainty": [], "sections": [], "raw_outputs": []}

    def n_plan(self, st: WorkflowState) -> dict:
        if st.get("forced_task"):
            subs = [st["user_request"]]
            tasks = [{"question": st["user_request"], "task": st["forced_task"]}]
        else:
            subs = decompose(st["user_request"])
            tasks = [{"question": s, "task": classify(s)} for s in subs]
        return {"subquestions": subs, "tasks": tasks, "cursor": 0, "task": tasks[0]["task"]}

    def _route(self, st: WorkflowState) -> str:
        if self.ctx.deadline and time.time() > self.ctx.deadline and st["cursor"] < len(st["tasks"]):
            return "verify"
        if st["cursor"] >= len(st["tasks"]):
            return "verify"
        return NODE_OF[st["tasks"][st["cursor"]]["task"]]

    def n_select(self, st: WorkflowState) -> dict:
        if self.ctx.deadline and time.time() > self.ctx.deadline and st["cursor"] < len(st["tasks"]):
            skipped = len(st["tasks"]) - st["cursor"]
            return {"errors": st["errors"] + [f"Workflow time budget exceeded; {skipped} sub-task(s) skipped (partial result)."]}
        return {}

    def _run(self, st: WorkflowState) -> dict:
        t = st["tasks"][st["cursor"]]
        mod = SPECIALISTS[t["task"]]
        errors = list(st["errors"])
        t0 = time.time()
        try:
            out: AgentOutput = mod.run(self.ctx, t["question"], t["task"], st.get("options") or {})
        except Exception as e:  # specialist failure -> partial result, never a fake success
            out = AgentOutput(errors=[f"{t['task']} stage failed: {type(e).__name__}: {str(e)[:200]}"])
            errors.append(out.errors[0])
        section = {"task": t["task"], "question": t["question"], "elapsed_s": round(time.time() - t0, 2)}
        return {"raw_outputs": st["raw_outputs"] + [(section, out)], "cursor": st["cursor"] + 1, "errors": errors}

    n_analyst = n_quality = n_impact = n_tests = n_docs = _run

    def n_verify(self, st: WorkflowState) -> dict:
        totals = {"total": 0, "citation_valid": 0, "unverified": 0, "removed": 0, "uncited": 0, "claims_with_evidence": 0}
        sections = []
        for section, out in st["raw_outputs"]:
            claims, stats = verify_claims(out.claims, self.ctx.registry, self.checker, self.ctx.settings.strict_claims)
            for k in totals:
                totals[k] += stats.get(k, 0)
            section.update({"summary": out.summary, "mode": out.mode, "insufficient": out.insufficient, "notes": out.notes,
                            "errors": out.errors, "claims": [c.to_dict() for c in claims], "payload": out.payload})
            sections.append(section)
        totals["citation_coverage"] = round(totals["claims_with_evidence"] / totals["total"], 3) if totals["total"] else None
        totals["note"] = ("Citation validity means the cited lines exist in the indexed revision and match the stored chunk; "
                          "it does not prove the claim's interpretation is correct.")
        findings = [f for s in sections for f in s["payload"].get("findings", [])]
        return {"sections": sections, "citation_validation": totals, "findings": findings}

    def n_assemble(self, st: WorkflowState) -> dict:
        secs = st["sections"]
        unc = list(st["uncertainty"])
        for s in secs:
            unc += s["notes"]
        all_insufficient = bool(secs) and all(s["insufficient"] for s in secs)
        errs = list(st["errors"]) + [e for s in secs for e in s["errors"]]
        status = "insufficient_evidence" if all_insufficient else ("partial" if errs else "complete")
        used = sorted({e for s in secs for c in s["claims"] for e in c["evidence_ids"]})
        reg = self.ctx.registry
        evidence = [i.to_dict(include_text=True) for i in reg.all() if i.id in used]
        for e in evidence:
            e["text"] = "\n".join(e["text"].split("\n")[:60])
        modes = {s["mode"] for s in secs}
        result = {
            "repository_id": st["repository_id"], "revision": st["revision"], "request": st["user_request"],
            "tasks": [t["task"] for t in st["tasks"]], "status": status,
            "summary": " ".join(s["summary"] for s in secs if s["summary"])[:2500],
            "sections": secs, "evidence": evidence, "verification": st["citation_validation"],
            "uncertainty": list(dict.fromkeys(unc)), "errors": errs,
            "ai": {"used": any("llm" in m for m in modes), "model": self.model if any("llm" in m for m in modes) else None,
                   "error": self.ctx.llm_error or None},
            "stale_index": self.ctx.stale, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return {"result": result, "status": status}

    # ---------------------------------------------------------------- engines
    def run(self, request: str, task: str | None = None, options: dict | None = None) -> dict:
        init: WorkflowState = {"user_request": request, "repository_id": self.ctx.repo_id,
                               "revision": self.ctx.repo["revision"], "forced_task": task, "options": options or {}}
        t0 = time.time()
        try:
            import langgraph.graph as lg  # noqa: F401
            engine = "langgraph"
            final = self._run_langgraph(init)
        except ImportError:
            engine = "sequential-fallback (langgraph not installed)"
            final = self._run_sequential(init)
        res = final["result"]
        res["engine"] = engine
        res["elapsed_s"] = round(time.time() - t0, 2)
        return res

    def _run_sequential(self, st: WorkflowState) -> WorkflowState:
        st = {**st, **self.n_classify(st)}
        st.update(self.n_plan(st))
        while True:
            st.update(self.n_select(st))
            nxt = self._route(st)
            if nxt == "verify":
                break
            st.update(getattr(self, "n_" + nxt)(st))
        st.update(self.n_verify(st))
        st.update(self.n_assemble(st))
        return st

    def _run_langgraph(self, init: WorkflowState) -> WorkflowState:
        from langgraph.graph import END, StateGraph
        g = StateGraph(WorkflowState)
        for name in ("classify", "plan", "select", "analyst", "quality", "impact", "tests", "docs", "verify", "assemble"):
            g.add_node(name, getattr(self, "n_" + name))
        g.set_entry_point("classify")
        g.add_edge("classify", "plan")
        g.add_edge("plan", "select")
        g.add_conditional_edges("select", self._route, {"analyst": "analyst", "quality": "quality", "impact": "impact",
                                                        "tests": "tests", "docs": "docs", "verify": "verify"})
        for name in ("analyst", "quality", "impact", "tests", "docs"):
            g.add_edge(name, "select")
        g.add_edge("verify", "assemble")
        g.add_edge("assemble", END)
        return g.compile().invoke(init, {"recursion_limit": 60})
