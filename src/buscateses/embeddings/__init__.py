"""Embedding engine factory."""

from __future__ import annotations

from typing import Any

from buscateses.config import Paths, Settings, get_paths
from buscateses.embeddings.base import (
    DOCUMENT_TEMPLATE,
    QUERY_TEMPLATE,
    Embedder,
    EmbeddingUnavailable,
    format_document,
    format_query,
    truncate_normalize,
)
from buscateses.embeddings.local_onnx import LocalOnnxEmbedder
from buscateses.embeddings.openai_compat import OpenAICompatibleEmbedder


def build_embedder(
    settings: Settings,
    paths: Paths | None = None,
    *,
    home: str | None = None,
) -> Embedder | None:
    """Build the configured embedder without loading heavy model state.

    ``None`` means embeddings are disabled. A local embedder may be returned
    while not ready; callers must consult :meth:`Embedder.ready`.
    """
    engine = settings.embedding.engine.strip().lower()
    if engine == "none":
        return None
    if engine == "local":
        resolved_paths = paths or get_paths(home)
        return LocalOnnxEmbedder(resolved_paths.model_dir, settings.embedding.local_variant)
    if engine in {"openai", "openai_compatible"}:
        base_url = settings.embedding.base_url.strip()
        model = settings.embedding.model.strip()
        if not base_url or not model:
            return None
        return OpenAICompatibleEmbedder(base_url, model, settings.embedding.api_key)
    raise EmbeddingUnavailable("Motor de embeddings desconhecido na configuração.")


def get_embedder(
    settings: Settings,
    paths: Paths | None = None,
    **kwargs: Any,
) -> Embedder | None:
    """Backward-compatible alias for :func:`build_embedder`."""
    return build_embedder(settings, paths, **kwargs)


__all__ = [
    "DOCUMENT_TEMPLATE",
    "QUERY_TEMPLATE",
    "Embedder",
    "EmbeddingUnavailable",
    "LocalOnnxEmbedder",
    "OpenAICompatibleEmbedder",
    "build_embedder",
    "format_document",
    "format_query",
    "get_embedder",
    "truncate_normalize",
]
