"""Vector storage: ChromaDB (persistent, local) with a SQLite+NumPy fallback."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from config.db import Database
from config.settings import Settings

log = logging.getLogger(__name__)


class VectorStoreError(Exception):
    pass


class SQLiteVectorStore:
    name = "sqlite-numpy"

    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[str, tuple[list[str], np.ndarray]] = {}

    def upsert(self, repo_id: str, ids: list[str], vectors: np.ndarray, metadatas: list[dict] | None = None) -> None:
        self.db.executemany("INSERT OR REPLACE INTO vectors(chunk_id,repo_id,dim,vec) VALUES(?,?,?,?)",
                            [(i, repo_id, int(v.shape[0]), np.asarray(v, dtype=np.float32).tobytes())
                             for i, v in zip(ids, vectors)])
        self._cache.pop(repo_id, None)

    def delete(self, repo_id: str, ids: list[str]) -> None:
        self.db.executemany("DELETE FROM vectors WHERE chunk_id=?", [(i,) for i in ids])
        self._cache.pop(repo_id, None)

    def delete_repo(self, repo_id: str) -> None:
        self.db.execute("DELETE FROM vectors WHERE repo_id=?", (repo_id,))
        self._cache.pop(repo_id, None)

    def count(self, repo_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM vectors WHERE repo_id=?", (repo_id,))["c"]

    def _load(self, repo_id: str):
        if repo_id not in self._cache:
            rows = self.db.query("SELECT chunk_id, dim, vec FROM vectors WHERE repo_id=?", (repo_id,))
            ids = [r["chunk_id"] for r in rows]
            mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 1), np.float32)
            self._cache[repo_id] = (ids, mat)
        return self._cache[repo_id]

    def query(self, repo_id: str, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        ids, mat = self._load(repo_id)
        if not ids:
            return []
        if mat.shape[1] != vector.shape[0]:
            raise VectorStoreError("Vector dimension mismatch: re-index the repository with the current embedding model.")
        sims = mat @ vector.astype(np.float32)
        top = np.argsort(-sims)[:k]
        return [(ids[i], float(sims[i])) for i in top]


class ChromaVectorStore:
    name = "chromadb"

    def __init__(self, path: Path):
        try:
            import chromadb
            from chromadb.config import Settings as CS
        except ImportError as e:
            raise VectorStoreError("chromadb is not installed") from e
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            self.client = chromadb.PersistentClient(path=str(path), settings=CS(anonymized_telemetry=False))
        except Exception as e:
            raise VectorStoreError(f"ChromaDB could not start: {type(e).__name__}: {e}") from e

    def _col(self, repo_id: str):
        return self.client.get_or_create_collection(f"cm_{repo_id}".replace("-", "_")[:63], metadata={"hnsw:space": "cosine"})

    def upsert(self, repo_id: str, ids: list[str], vectors: np.ndarray, metadatas: list[dict] | None = None) -> None:
        col = self._col(repo_id)
        for i in range(0, len(ids), 2000):
            col.upsert(ids=ids[i:i + 2000], embeddings=vectors[i:i + 2000].tolist(),
                       metadatas=(metadatas[i:i + 2000] if metadatas else None))

    def delete(self, repo_id: str, ids: list[str]) -> None:
        if ids:
            col = self._col(repo_id)
            for i in range(0, len(ids), 2000):
                col.delete(ids=ids[i:i + 2000])

    def delete_repo(self, repo_id: str) -> None:
        try:
            self.client.delete_collection(f"cm_{repo_id}".replace("-", "_")[:63])
        except Exception:
            pass

    def count(self, repo_id: str) -> int:
        return self._col(repo_id).count()

    def query(self, repo_id: str, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        col = self._col(repo_id)
        n = col.count()
        if n == 0:
            return []
        try:
            r = col.query(query_embeddings=[vector.tolist()], n_results=min(k, n))
        except Exception as e:
            raise VectorStoreError(f"ChromaDB query failed: {type(e).__name__}: {e}") from e
        return [(i, 1.0 - float(d)) for i, d in zip(r["ids"][0], r["distances"][0])]


def get_vector_store(settings: Settings, db: Database):
    if settings.vector_backend in {"auto", "chroma"}:
        try:
            return ChromaVectorStore(settings.vectors_dir)
        except VectorStoreError as e:
            if settings.vector_backend == "chroma":
                raise
            log.info("Falling back to SQLite vector store: %s", e)
    return SQLiteVectorStore(db)
