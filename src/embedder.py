"""
Centralised embedding singleton.

All modules import from here so the model is loaded exactly once per process.
Model: paraphrase-multilingual-MiniLM-L12-v2
  • 384 dimensions  • Chinese / multilingual  • Apache 2.0
"""

from __future__ import annotations
from sentence_transformers import SentenceTransformer

_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_embedder: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(_MODEL)
    return _embedder


def embed(text: str) -> list[float]:
    return get_embedder().encode([text])[0].tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in get_embedder().encode(texts)]


def vec_sql(v: list[float]) -> str:
    """Convert a Python float list to SeekDB VECTOR literal '[a,b,…]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
