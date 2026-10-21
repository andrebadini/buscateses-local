"""Embedding protocol, prompts and Matryoshka helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

QUERY_TEMPLATE = "task: search result | query: {query}"
DOCUMENT_TEMPLATE = "title: {title} | text: {text}"


class EmbeddingUnavailable(RuntimeError):
    """Raised when embeddings cannot be produced locally or remotely."""


@runtime_checkable
class Embedder(Protocol):
    """Common interface for local and OpenAI-compatible embedding engines."""

    name: str
    dims_full: int

    def ready(self) -> bool:
        """Return whether the engine can currently embed texts."""
        ...

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Embed raw query strings, returning L2-normalized ``[n, 768]``."""
        ...

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> np.ndarray:
        """Embed ``(title, text)`` documents, returning normalized ``[n, 768]``."""
        ...


def format_query(query: str) -> str:
    """Apply the EmbeddingGemma query prompt."""
    return QUERY_TEMPLATE.format(query=str(query))


def format_document(title: str | None, text: str | None) -> str:
    """Apply the EmbeddingGemma document prompt."""
    return DOCUMENT_TEMPLATE.format(title=title or "none", text=text or "")


def truncate_normalize(matrix: np.ndarray | Sequence[Sequence[float]], dims: int) -> np.ndarray:
    """Truncate the last dimension to ``dims`` and re-normalize rows (float32).

    Zero vectors remain zero. The returned array is always C-contiguous.
    """
    if dims <= 0:
        raise ValueError("dims deve ser positivo")
    array = np.asarray(matrix, dtype=np.float32)
    if array.ndim == 1:
        array = array.reshape(1, -1)
        squeeze = True
    elif array.ndim == 2:
        squeeze = False
    else:
        raise ValueError("A matriz de embeddings deve ter uma ou duas dimensões.")
    if array.shape[1] == 0:
        raise ValueError("A matriz de embeddings está vazia.")
    truncated = np.ascontiguousarray(array[:, : min(dims, array.shape[1])], dtype=np.float32)
    norms = np.linalg.norm(truncated, axis=1, keepdims=True)
    normalized = np.divide(
        truncated,
        norms,
        out=np.zeros_like(truncated),
        where=norms > 1e-12,
    )
    normalized = np.ascontiguousarray(normalized, dtype=np.float32)
    return normalized[0] if squeeze else normalized


__all__ = [
    "DOCUMENT_TEMPLATE",
    "QUERY_TEMPLATE",
    "Embedder",
    "EmbeddingUnavailable",
    "format_document",
    "format_query",
    "truncate_normalize",
]
