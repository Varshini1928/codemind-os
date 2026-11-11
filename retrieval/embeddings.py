"""Local embeddings. Source text never leaves the machine.

Backends: sentence-transformers (neural, local) or a deterministic hashing fallback (lexical-ish, NOT semantic).
The backend actually used is recorded with the index and reported to the user.
"""
from __future__ import annotations

import hashlib
import math
import re

import numpy as np

from config.settings import Settings
from retrieval.lexical_search import tokenize

MAX_CHARS = 2000


class EmbeddingError(Exception):
    pass


class Embedder:
    name = "base"
    dim = 0
    is_semantic = False
    fallback_reason = ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class HashingEmbedder(Embedder):
    """Feature-hashing of identifier sub-tokens and bigrams into a fixed-size L2-normalised vector.
    Deterministic and dependency-free. It captures vocabulary overlap, not meaning."""
    name = "hashing-v1"
    dim = 384
    is_semantic = False

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text[:MAX_CHARS])
        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for t, c in counts.items():
            h = int.from_bytes(hashlib.md5(t.encode()).digest()[:8], "little")
            v[h % self.dim] += (1.0 + math.log(c)) * (1 if (h >> 40) & 1 else -1)
        for a, b in zip(toks, toks[1:]):
            h = int.from_bytes(hashlib.md5(f"{a} {b}".encode()).digest()[:8], "little")
            v[h % self.dim] += 0.5 * (1 if (h >> 40) & 1 else -1)
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class SentenceTransformerEmbedder(Embedder):
    is_semantic = True

    def __init__(self, settings: Settings):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbeddingError("sentence-transformers is not installed") from e
        try:
            self.model = SentenceTransformer(settings.embedding_model, cache_folder=str(settings.models_dir),
                                             local_files_only=not settings.allow_model_download, device="cpu")
        except Exception as e:  # missing weights, offline, corrupt cache ...
            raise EmbeddingError(
                f"Embedding model '{settings.embedding_model}' is not available locally ({type(e).__name__}). "
                "Download it explicitly (see scripts/check_environment.py --download-embedding-model) "
                "or set CODEMIND_ALLOW_MODEL_DOWNLOAD=true.") from e
        self.name = f"sentence-transformers:{settings.embedding_model}"
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        try:
            return np.asarray(self.model.encode([t[:MAX_CHARS] for t in texts], batch_size=32,
                                                normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)
        except Exception as e:
            raise EmbeddingError(f"Embedding failed: {type(e).__name__}: {e}") from e


_cache: dict[tuple, Embedder] = {}


def get_embedder(settings: Settings) -> Embedder:
    key = (settings.embedding_backend, settings.embedding_model, settings.allow_model_download, str(settings.models_dir))
    if key in _cache:
        return _cache[key]
    emb: Embedder
    if settings.embedding_backend == "hashing":
        emb = HashingEmbedder()
    else:
        try:
            emb = SentenceTransformerEmbedder(settings)
        except EmbeddingError as e:
            if settings.embedding_backend == "sentence-transformers":
                raise
            emb = HashingEmbedder()
            emb.fallback_reason = str(e)
    _cache[key] = emb
    return emb


def embedding_text(path: str, symbol: str, text: str) -> str:
    return f"{path} {symbol}\n{text}"
