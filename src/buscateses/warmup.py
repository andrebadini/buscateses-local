"""Background warm-up so the first user search does not pay cold-cache costs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def warm_up(
    *,
    embedder: Any | None = None,
    catalog: Any | None = None,
) -> None:
    """Load the embedding model and touch catalog pages.

    Both steps are best-effort: a missing model or catalog must never prevent
    the application from starting.
    """
    if embedder is not None:
        try:
            warm = getattr(embedder, "warm_up", None)
            if callable(warm):
                warm()
            elif embedder.ready():
                embedder.embed_queries(["aquecimento"])
        except Exception:  # noqa: BLE001 - optional engine
            logger.debug("Não foi possível aquecer o modelo de embedding.", exc_info=True)
    if catalog is not None:
        try:
            warm_catalog = getattr(catalog, "warm_up", None)
            if callable(warm_catalog):
                warm_catalog()
        except Exception:  # noqa: BLE001 - cache warming is optional
            logger.debug("Não foi possível aquecer o catálogo.", exc_info=True)


def start_warmup(
    *,
    embedder: Any | None = None,
    catalog: Any | None = None,
    catalog_provider: Callable[[], Any | None] | None = None,
) -> threading.Thread:
    """Start :func:`warm_up` on a daemon thread and return it immediately.

    ``catalog_provider`` is called inside the worker so opening the release
    never blocks the UI thread.
    """

    def worker() -> None:
        resolved = catalog
        if resolved is None and catalog_provider is not None:
            try:
                resolved = catalog_provider()
            except Exception:  # noqa: BLE001 - startup must continue
                logger.debug("Não foi possível abrir o catálogo para aquecimento.", exc_info=True)
                resolved = None
        warm_up(embedder=embedder, catalog=resolved)

    thread = threading.Thread(target=worker, name="buscateses-warmup", daemon=True)
    thread.start()
    return thread


__all__ = ["start_warmup", "warm_up"]
