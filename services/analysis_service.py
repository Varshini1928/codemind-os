"""Facade used by the API, CLI and dashboard: builds the agent context and runs the workflow."""
from __future__ import annotations

import time
from dataclasses import dataclass

from agents.common import AgentContext
from agents.llm_client import LLMError, OllamaClient
from agents.workflow import Workflow
from config.settings import Settings
from graph.graph_store import GraphStore
from retrieval.chunk_store import ChunkStore
from retrieval.evidence_assembler import EvidenceRegistry
from retrieval.hybrid_search import HybridSearcher, IndexNotReadyError
from services.indexing_service import IndexingService
from services.report_service import ReportService
from verification.citation_checker import CitationChecker


class AnalysisService:
    def __init__(self, settings: Settings, indexing: IndexingService, chunks: ChunkStore, searcher: HybridSearcher,
                 graphs: GraphStore, reports: ReportService):
        self.s, self.indexing, self.chunks, self.searcher, self.graphs, self.reports = settings, indexing, chunks, searcher, graphs, reports
        self._health: tuple[float, dict] | None = None

    def llm_status(self, force: bool = False) -> dict:
        if not force and self._health and time.time() - self._health[0] < 20:
            return self._health[1]
        try:
            h = OllamaClient(self.s).health()
        except LLMError as e:
            h = {"available": False, "model": self.s.ollama_model, "model_installed": False, "models": [], "error": str(e)}
        self._health = (time.time(), h)
        return h

    def _context(self, repo_id: str, use_llm: bool) -> tuple[AgentContext, CitationChecker, str]:
        repo = self.indexing.require(repo_id)
        if repo["status"] != "ready":
            raise IndexNotReadyError(f"Repository index is '{repo['status']}'; run indexing first.")
        stale = self.indexing.is_stale(repo_id)
        if stale and self.s.require_fresh_index:
            raise IndexNotReadyError("The repository changed since indexing and require_fresh_index is enabled.")
        reg = EvidenceRegistry(repo_id, repo["revision"], self.chunks)
        ctx = AgentContext(self.s, repo, reg, self.searcher, self.chunks, lambda: self.graphs.load(repo_id), None, stale,
                           time.time() + self.s.workflow_timeout_s)
        model = ""
        if not use_llm:
            ctx.llm_error = "AI interpretation disabled for this request"
        else:
            h = self.llm_status()
            if h["available"] and h["model_installed"]:
                ctx.llm = OllamaClient(self.s)
                model = self.s.ollama_model
            else:
                ctx.llm_error = h["error"] or "local model unavailable"
        from pathlib import Path
        checker = CitationChecker(repo_id, Path(repo["root"]), repo["revision"], self.chunks)
        return ctx, checker, model

    def run(self, repo_id: str, request: str, task: str | None = None, options: dict | None = None,
            use_llm: bool = True, save: bool = True) -> dict:
        ctx, checker, model = self._context(repo_id, use_llm)
        result = Workflow(ctx, checker, model).run(request, task, options)
        if save:
            kind = task or (result["tasks"][0] if result["tasks"] else "analysis")
            result["report"] = self.reports.save(repo_id, kind, result)
        return result

    # convenience wrappers ------------------------------------------------
    def ask(self, repo_id, question, **kw): return self.run(repo_id, question, None, None, **kw)
    def architecture(self, repo_id, **kw): return self.run(repo_id, "Describe the repository architecture", "architecture", None, **kw)
    def quality_review(self, repo_id, focus="", paths=None, **kw): return self.run(repo_id, focus or "Review code quality and security", "quality", {"paths": paths or []}, **kw)
    def impact(self, repo_id, target, change="", depth=None, **kw): return self.run(repo_id, change or f"Impact of changing {target}", "impact", {"target": target, "change": change, "depth": depth}, **kw)
    def test_plan(self, repo_id, target="", **kw): return self.run(repo_id, f"Test plan for {target or 'the repository'}", "tests", {"target": target}, **kw)
    def document(self, repo_id, kind="full", **kw): return self.run(repo_id, "Generate documentation", "documentation", {"kind": kind}, **kw)
