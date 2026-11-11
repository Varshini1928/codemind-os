"""Incremental indexing pipeline: manifest diff -> parse changed files -> graph -> chunks -> embeddings."""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.db import Database
from config.settings import Settings
from graph.graph_builder import build_graph
from graph.graph_store import GraphStore
from ingestion.file_filter import decode_bytes
from ingestion.manifest import Manifest, diff_manifests
from ingestion.repository_loader import RepositoryLoader
from parsing.entity_extractor import ParseResult, parse_file
from retrieval.chunk_store import ChunkStore
from retrieval.chunker import chunk_file
from retrieval.embeddings import EmbeddingError, embedding_text
from services.job_service import JobCancelled
from verification.safety_policy import redact_secrets, resolve_inside, validate_repository_path

Progress = Callable[[float, str], None]


class IndexingError(Exception):
    pass


class IncrementalIndexer:
    def __init__(self, settings: Settings, db: Database, loader: RepositoryLoader, chunks: ChunkStore, vectors,
                 graphs: GraphStore, embedder_factory, on_change: Callable[[str], None]):
        self.s, self.db, self.loader, self.chunks, self.vectors, self.graphs = settings, db, loader, chunks, vectors, graphs
        self.embedder_factory, self.on_change = embedder_factory, on_change

    def _wipe(self, repo_id: str) -> None:
        self.vectors.delete_repo(repo_id)
        self.chunks.delete_repo(repo_id)
        self.db.execute("DELETE FROM parses WHERE repo_id=?", (repo_id,))
        self.db.execute("DELETE FROM manifests WHERE repo_id=?", (repo_id,))
        self.graphs.delete(repo_id)

    def wipe(self, repo_id: str) -> None:
        self._wipe(repo_id)
        self.on_change(repo_id)

    def run(self, repo: dict, full: bool = False, progress: Progress | None = None,
            cancel: threading.Event | None = None) -> dict:
        repo_id = repo["id"]
        prog = progress or (lambda f, m: None)

        def check():
            if cancel is not None and cancel.is_set():
                raise JobCancelled()

        free_mb = shutil.disk_usage(self.s.storage_dir).free // (1024 * 1024)
        if free_mb < self.s.min_free_disk_mb:
            raise IndexingError(f"Insufficient disk space: {free_mb} MB free, {self.s.min_free_disk_mb} MB required.")
        self.db.execute("UPDATE repositories SET status='indexing', last_error=NULL WHERE id=?", (repo_id,))
        warnings: list[str] = []
        try:
            root = validate_repository_path(repo["root"], self.s)
            info = self.loader.inspect(str(root))
            prog(0.02, "Scanning repository files (read-only)")
            manifest = self.loader.build_manifest(root)
            check()
            if manifest.truncated:
                warnings.append(f"File limit reached ({self.s.max_files}); the index is incomplete.")
            if not manifest.files:
                warnings.append("No indexable files were found (empty repository or everything was excluded).")
            embedder = self.embedder_factory()
            if embedder.fallback_reason:
                warnings.append("Using hashing fallback embeddings: " + embedder.fallback_reason)
            old_row = self.db.one("SELECT json FROM manifests WHERE repo_id=?", (repo_id,))
            old = None if (full or not old_row) else Manifest.from_json(old_row["json"])
            if repo.get("embedder") and repo["embedder"] != embedder.name:
                prog(0.05, "Embedding model changed: all chunks will be re-embedded")
                self.vectors.delete_repo(repo_id)
                self.chunks.reset_embedded(repo_id)
            if old is None:
                self._wipe(repo_id)
            diff = diff_manifests(old, manifest)
            revision = self.loader.revision_id(info, manifest)
            prog(0.08, f"Changes: +{len(diff.added)} ~{len(diff.modified)} -{len(diff.deleted)} "
                       f"={len(diff.unchanged)} unchanged")
            # ---- remove obsolete data
            obsolete = diff.deleted + diff.modified
            if obsolete:
                self.vectors.delete(repo_id, self.chunks.delete_paths(repo_id, obsolete))
                for p in diff.deleted:
                    self.db.execute("DELETE FROM parses WHERE repo_id=? AND path=?", (repo_id, p))
            # ---- parse changed files
            parsed_now: dict[str, ParseResult] = {}
            skipped = []
            changed = diff.changed
            for i, p in enumerate(changed):
                check()
                if i % 20 == 0:
                    prog(0.1 + 0.3 * i / max(1, len(changed)), f"Parsing {i}/{len(changed)}: {p}")
                try:
                    data = resolve_inside(root, p).read_bytes()
                except Exception:
                    skipped.append(p)
                    warnings.append(f"Unreadable during indexing: {p}")
                    continue
                import hashlib
                if hashlib.sha256(data).hexdigest() != manifest.files[p]["sha256"]:
                    skipped.append(p)
                    warnings.append(f"File changed while indexing, skipped: {p} (re-run indexing)")
                    continue
                text, _enc = decode_bytes(data)
                pr = parse_file(p, text, manifest.files[p]["language"])
                parsed_now[p] = pr
                self.db.execute("INSERT OR REPLACE INTO parses(repo_id,path,sha256,json) VALUES(?,?,?,?)",
                                (repo_id, p, manifest.files[p]["sha256"], json.dumps(pr.to_dict())))
                if pr.status in {"syntax_error", "failed"}:
                    warnings.append(f"{p}: {pr.status} - {'; '.join(pr.errors)[:160]}")
            # ---- graph (rebuilt deterministically from cached per-file parses)
            check()
            prog(0.45, "Building knowledge graph")
            parses = {r["path"]: ParseResult.from_dict(json.loads(r["json"]))
                      for r in self.db.query("SELECT path,json FROM parses WHERE repo_id=?", (repo_id,))}
            parses = {p: pr for p, pr in parses.items() if p in manifest.files}
            g = build_graph(repo_id, revision, manifest.files, parses)
            self.graphs.save(repo_id, g)
            # ---- chunks
            check()
            prog(0.55, "Chunking changed files")
            n_new = 0
            for p in changed:
                if p in skipped or p not in parsed_now:
                    continue
                text = decode_bytes(resolve_inside(root, p).read_bytes())[0]
                info_f = manifest.files[p]
                new = chunk_file(repo_id, revision, p, info_f["language"], text, parsed_now[p],
                                 self.s.max_chunk_lines, info_f["mtime"])
                self.chunks.add(new)
                n_new += len(new)
            self.chunks.set_revision(repo_id, revision)
            # ---- embeddings
            pending = self.chunks.pending_count(repo_id)
            done = 0
            while pending and done < pending + 1_000_000:
                check()
                batch = self.chunks.pending_embedding(repo_id, 64)
                if not batch:
                    break
                try:
                    vecs = embedder.embed_documents([embedding_text(c["path"], c["symbol"], c["text"]) for c in batch])
                    self.vectors.upsert(repo_id, [c["id"] for c in batch], vecs,
                                        [{"path": c["path"], "symbol": c["symbol"], "start": c["start_line"],
                                          "end": c["end_line"]} for c in batch])
                    self.chunks.mark_embedded([c["id"] for c in batch])
                except Exception as e:  # EmbeddingError, VectorStoreError, chroma errors
                    warnings.append(f"Embedding stopped: {type(e).__name__}: {redact_secrets(str(e))[:200]}. "
                                    "Lexical and graph retrieval still work; re-run indexing to retry.")
                    break
                done += len(batch)
                prog(0.6 + 0.35 * done / max(1, pending), f"Embedded {done}/{pending} chunks")
            n_entities = sum(1 for _, d in g.nodes(data=True) if d.get("type") not in {"Repository", "Directory", "File"})
            sig = self.loader.signature(root)
            self.db.execute(
                "UPDATE repositories SET status='ready', revision=?, head=?, branch=?, indexed_at=?, signature=?, "
                "manifest_digest=?, file_count=?, chunk_count=?, entity_count=?, embedder=?, embedder_dim=?, "
                "warnings=?, last_error=NULL, truncated=? WHERE id=?",
                (revision, info.head, info.branch, datetime.now(timezone.utc).isoformat(timespec="seconds"), sig,
                 manifest.digest(), len(manifest.files), self.chunks.count(repo_id), n_entities, embedder.name,
                 embedder.dim, json.dumps(warnings[:200]), int(manifest.truncated), repo_id))
            self.db.execute("INSERT OR REPLACE INTO manifests(repo_id,json) VALUES(?,?)", (repo_id, manifest.to_json()))
            self.on_change(repo_id)
            prog(1.0, "Indexing complete")
            return {"repo_id": repo_id, "revision": revision, "added": len(diff.added), "modified": len(diff.modified),
                    "deleted": len(diff.deleted), "unchanged": len(diff.unchanged), "new_chunks": n_new,
                    "pending_embeddings": self.chunks.pending_count(repo_id), "warnings": warnings[:50],
                    "excluded": manifest.excluded_counts, "embedder": embedder.name, "full": old is None}
        except JobCancelled:
            self.db.execute("UPDATE repositories SET status='cancelled', signature=NULL WHERE id=?", (repo_id,))
            self.on_change(repo_id)
            raise
        except Exception as e:
            self.db.execute("UPDATE repositories SET status='failed', last_error=?, signature=NULL WHERE id=?",
                            (redact_secrets(f"{type(e).__name__}: {e}")[:500], repo_id))
            self.on_change(repo_id)
            raise
