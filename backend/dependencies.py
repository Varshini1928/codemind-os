"""Service container shared by the API (which serves the web UI) and the CLI (all in-process, all local)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from config.db import Database
from config.settings import Settings, load_settings
from graph.graph_store import GraphStore
from retrieval.chunk_store import ChunkStore
from retrieval.embeddings import get_embedder
from retrieval.hybrid_search import HybridSearcher
from retrieval.vector_store import get_vector_store
from services.analysis_service import AnalysisService
from services.auth_service import AuthService
from services.indexing_service import IndexingService
from services.job_service import JobService
from services.report_service import ReportService
from verification.safety_policy import RedactingFilter


@dataclass
class Services:
    settings: Settings
    db: Database
    chunks: ChunkStore
    vectors: object
    graphs: GraphStore
    jobs: JobService
    indexing: IndexingService
    searcher: HybridSearcher
    reports: ReportService
    analysis: AnalysisService
    auth: AuthService


def build_services(settings: Settings | None = None) -> Services:
    s = settings or load_settings()
    s.ensure_dirs()
    logging.getLogger().addFilter(RedactingFilter())
    db = Database(s.db_path)
    # persisted, whitelisted runtime overrides (model name, weights, ...)
    from config.settings import RUNTIME_OVERRIDABLE
    for row in db.query("SELECT key,value FROM settings_overrides"):
        if row["key"] in RUNTIME_OVERRIDABLE:
            cur = getattr(s, row["key"])
            setattr(s, row["key"], type(cur)(row["value"]) if not isinstance(cur, bool) else row["value"] == "True")
    chunks, graphs = ChunkStore(db), GraphStore(s.graphs_dir)
    vectors = get_vector_store(s, db)
    jobs = JobService(db)
    emb = lambda: get_embedder(s)  # noqa: E731
    holder: dict = {}
    indexing = IndexingService(s, db, chunks, vectors, graphs, jobs, emb, lambda rid: holder["searcher"].invalidate(rid))
    searcher = HybridSearcher(s, chunks, vectors, graphs, emb, indexing.get_repo)
    holder["searcher"] = searcher
    reports = ReportService(s)
    analysis = AnalysisService(s, indexing, chunks, searcher, graphs, reports)

    def purge_user_data(user_id: str) -> None:
        import shutil
        for r in indexing.list_repos(user_id):
            indexing.unregister(r["id"])
            shutil.rmtree(s.reports_dir / r["id"], ignore_errors=True)
            shutil.rmtree(s.review_dir / r["id"], ignore_errors=True)

    auth = AuthService(db, s, purge_user_data)
    if s.admin_email and s.admin_password:
        auth.seed_admin(s.admin_email, s.admin_password)
    return Services(s, db, chunks, vectors, graphs, jobs, indexing, searcher, reports, analysis, auth)


@lru_cache(maxsize=1)
def get_services() -> Services:
    return build_services()


def set_override(svc: Services, key: str, value) -> None:
    from config.settings import RUNTIME_OVERRIDABLE
    if key not in RUNTIME_OVERRIDABLE:
        raise ValueError(f"'{key}' cannot be changed at runtime (environment-only setting).")
    cur = getattr(svc.settings, key)
    setattr(svc.settings, key, type(cur)(value) if not isinstance(cur, bool) else bool(value))
    svc.db.execute("INSERT OR REPLACE INTO settings_overrides(key,value) VALUES(?,?)", (key, str(getattr(svc.settings, key))))
    svc.analysis._health = None
