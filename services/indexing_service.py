"""Repository registry + indexing orchestration + read-only repository views."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config.db import Database
from config.settings import Settings
from graph.graph_store import GraphStore
from ingestion.incremental_indexer import IncrementalIndexer
from ingestion.manifest import Manifest
from ingestion.repository_loader import RepositoryLoader
from retrieval.chunk_store import ChunkStore
from services.job_service import JobService
from verification.safety_policy import SecurityError


class NotFoundError(Exception):
    pass


class IndexingService:
    def __init__(self, settings: Settings, db: Database, chunks: ChunkStore, vectors, graphs: GraphStore,
                 jobs: JobService, embedder_factory, on_change):
        self.s, self.db, self.chunks, self.vectors, self.graphs, self.jobs = settings, db, chunks, vectors, graphs, jobs
        self.loader = RepositoryLoader(settings)
        self.indexer = IncrementalIndexer(settings, db, self.loader, chunks, vectors, graphs, embedder_factory, on_change)

    # ------------------------------------------------------------ registry
    @staticmethod
    def _repo(r) -> dict:
        d = dict(r)
        d["warnings"] = json.loads(d.get("warnings") or "[]")
        return d

    def get_repo(self, repo_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM repositories WHERE id=?", (repo_id,))
        return self._repo(r) if r else None

    def require(self, repo_id: str) -> dict:
        r = self.get_repo(repo_id)
        if r is None:
            raise NotFoundError(f"Repository '{repo_id}' is not registered.")
        return r

    def list_repos(self, owner_id: str | None = None) -> list[dict]:
        if owner_id is None:
            rows = self.db.query("SELECT * FROM repositories ORDER BY created_at DESC")
        else:
            rows = self.db.query("SELECT * FROM repositories WHERE owner_id=? ORDER BY created_at DESC", (owner_id,))
        return [self._repo(r) for r in rows]

    def count_owned(self, owner_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM repositories WHERE owner_id=?", (owner_id,))["c"]

    @staticmethod
    def _clone_name(url: str, owner_id: str = "") -> str:
        tail = re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(url.rstrip("/").removesuffix(".git").split("/")[-2:]))[:40].strip("-.")
        return f"{tail or 'repo'}-{hashlib.sha1((owner_id + '|' + url).encode()).hexdigest()[:8]}"

    def register(self, path: str | None = None, git_url: str | None = None, dest_name: str | None = None,
                 owner_id: str | None = None) -> dict:
        if git_url:
            dest = self.loader.clone(git_url, dest_name or self._clone_name(git_url.strip(), owner_id or ""))
            path = str(dest)
        elif path and self.s.hosted:
            raise SecurityError("Local paths are disabled on this deployment; register a public Git URL instead.")
        if not path:
            raise SecurityError("Provide a local path or a git URL.")
        info = self.loader.inspect(path)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self.get_repo(info.repo_id)
        if existing:
            if existing.get("owner_id") not in (None, owner_id):
                raise SecurityError("This repository is already registered by another account.")
            return existing
        self.db.execute(
            "INSERT INTO repositories(id,name,root,git_url,branch,head,status,created_at,owner_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (info.repo_id, info.name, str(info.root), git_url, info.branch, info.head, "registered", now, owner_id))
        return self.require(info.repo_id)

    def start_index(self, repo_id: str, full: bool = False) -> str:
        repo = self.require(repo_id)
        busy = [j for j in self.jobs.list(repo_id) if j["status"] in {"queued", "running"} and j["kind"] == "index"]
        if busy:
            return busy[0]["id"]
        return self.jobs.submit(repo_id, "index", lambda prog, ev: self.indexer.run(repo, full, prog, ev))

    def index_now(self, repo_id: str, full: bool = False, progress=None) -> dict:
        """Synchronous variant used by the CLI, tests and evaluation scripts."""
        return self.indexer.run(self.require(repo_id), full, progress)

    def delete_index(self, repo_id: str) -> None:
        self.require(repo_id)
        self.indexer.wipe(repo_id)
        self.db.execute("UPDATE repositories SET status='registered', revision=NULL, signature=NULL, file_count=0, "
                        "chunk_count=0, entity_count=0, embedder=NULL, warnings='[]', indexed_at=NULL WHERE id=?", (repo_id,))

    def unregister(self, repo_id: str) -> None:
        repo = self.require(repo_id)
        self.delete_index(repo_id)
        self.db.execute("DELETE FROM repositories WHERE id=?", (repo_id,))
        ws = self.s.clone_workspace
        root = Path(repo["root"])
        if ws is not None and repo.get("git_url") and root.resolve() != ws and ws in root.resolve().parents:
            shutil.rmtree(root, ignore_errors=True)  # remove only our own clone, never a user's local directory

    def is_stale(self, repo_id: str) -> bool:
        repo = self.require(repo_id)
        if repo["status"] != "ready" or not repo.get("signature"):
            return False
        try:
            return self.loader.signature(Path(repo["root"])) != repo["signature"]
        except OSError:
            return True

    def status(self, repo_id: str) -> dict:
        repo = self.require(repo_id)
        repo["stale"] = self.is_stale(repo_id)
        repo["pending_embeddings"] = self.chunks.pending_count(repo_id) if repo["status"] == "ready" else None
        return repo

    # ------------------------------------------------------------ read-only views
    def overview(self, repo_id: str) -> dict:
        repo = self.status(repo_id)
        out = {"repository": repo, "languages": {}, "entity_types": {}, "parse_status": {}, "excluded": {},
               "entry_points": [], "endpoints": [], "config_keys": [], "deployment_artifacts": [],
               "external_dependencies": {}, "top_directories": {}}
        if repo["status"] != "ready":
            return out
        g = self.graphs.load(repo_id)
        langs, types, pstat, dirs = Counter(), Counter(), Counter(), Counter()
        for n, d in g.nodes(data=True):
            t = d.get("type")
            if t == "File":
                langs[d.get("language") or "unknown"] += 1
                pstat[d.get("parse_status", "unparsed")] += 1
                top = d["path"].split("/")[0] if "/" in d["path"] else "(root)"
                dirs[top] += 1
            elif t not in {"Repository", "Directory"} and not d.get("external"):
                types[t] += 1
            if t == "Module" and d.get("attrs", {}).get("entry_point"):
                out["entry_points"].append({"path": d["path"], "module": d["qualified_name"], "line": d.get("start_line")})
            if t == "Endpoint":
                out["endpoints"].append({"route": d["name"], "path": d["path"], "line": d["start_line"],
                                         "handler": d.get("attrs", {}).get("handler"), "confidence": d.get("confidence")})
            if t == "ConfigKey":
                readers = [g.nodes[u].get("qualified_name") for u, _, e in g.in_edges(n, data=True) if e["type"] == "READS_CONFIG"]
                out["config_keys"].append({"key": d["name"], "declared_in": d.get("path") or None,
                                           "line": d.get("start_line"), "read_by": readers[:10]})
            if t == "DeploymentArtifact":
                out["deployment_artifacts"].append({"path": d["path"]})
        out.update(languages=dict(langs), entity_types=dict(types), parse_status=dict(pstat), top_directories=dict(dirs),
                   external_dependencies=dict(sorted(g.graph.get("external_imports", {}).items(), key=lambda kv: -kv[1])[:40]))
        mrow = self.db.one("SELECT json FROM manifests WHERE repo_id=?", (repo_id,))
        if mrow:
            out["excluded"] = Manifest.from_json(mrow["json"]).excluded_counts
        out["endpoints"].sort(key=lambda e: (e["path"], e["line"]))
        return out

    def list_files(self, repo_id: str, language: str | None = None, query: str | None = None) -> list[dict]:
        self.require(repo_id)
        mrow = self.db.one("SELECT json FROM manifests WHERE repo_id=?", (repo_id,))
        if not mrow:
            return []
        files = Manifest.from_json(mrow["json"]).files
        g = self.graphs.load(repo_id)
        out = []
        for p, info in sorted(files.items()):
            if language and info["language"] != language:
                continue
            if query and query.lower() not in p.lower():
                continue
            out.append({"path": p, "language": info["language"], "lines": info["lines"], "size": info["size"],
                        "parse_status": g.nodes.get(f"file:{p}", {}).get("parse_status", "unparsed")})
        return out

    def find_symbols(self, repo_id: str, query: str, limit: int = 50) -> list[dict]:
        self.require(repo_id)
        g = self.graphs.load(repo_id)
        q = query.lower()
        out = []
        for n, d in g.nodes(data=True):
            if d.get("type") in {"Function", "Method", "Class", "Test", "DatabaseEntity", "Endpoint", "Module", "ConfigKey"} \
                    and not d.get("external") and q in (d.get("qualified_name") or d.get("name", "")).lower():
                out.append({"id": n, "type": d["type"], "qualified_name": d.get("qualified_name"), "path": d.get("path"),
                            "start_line": d.get("start_line"), "end_line": d.get("end_line"),
                            "extraction_method": d.get("extraction_method"), "confidence": d.get("confidence")})
        out.sort(key=lambda x: (len(x["qualified_name"] or ""), x["qualified_name"] or ""))
        return out[:limit]
