"""Shared application state and lazy catalog resolution."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from buscateses.catalog.installer import CatalogInstaller
from buscateses.catalog.search import CatalogSearch, SearchError
from buscateses.config import Paths, Settings
from buscateses.embeddings import Embedder, build_embedder
from buscateses.jobs import JobManager
from buscateses.library.ingest import LibraryIngestor
from buscateses.library.store import LibraryStore
from buscateses.remote import RemoteController, save_remote_settings

logger = logging.getLogger(__name__)


class CatalogProvider:
    """Resolve the active release while allowing an injected test catalog."""

    def __init__(
        self,
        paths: Paths,
        installer: CatalogInstaller,
        embedder: Embedder | None,
        injected: Any | None = None,
    ) -> None:
        self.paths = paths
        self.installer = installer
        self.embedder = embedder
        self.injected = injected
        self._catalog: Any | None = injected
        self._key: tuple[str, str] | None = None
        self._lock = threading.RLock()

    def get(self) -> Any | None:
        """Return the injected catalog or a cached search controller."""
        with self._lock:
            if self.injected is not None:
                return self.injected
            release = self.installer.current_release_dir()
            if release is None:
                self._close_current()
                self._key = None
                return None
            key = (str(release), getattr(self.embedder, "name", "") or "none")
            if self._catalog is not None and self._key == key:
                return self._catalog
            self._close_current()
            try:
                self._catalog = CatalogSearch(release, self.embedder)
            except SearchError:
                self._catalog = None
                self._key = None
                return None
            self._key = key
            return self._catalog

    def invalidate(self) -> None:
        """Close the cached release and forget it after an update."""
        with self._lock:
            if self.injected is None:
                self._close_current()
                self._key = None

    def close(self) -> None:
        """Release SQLite handles and the vector memory map of the catalog."""
        with self._lock:
            if self.injected is None:
                self._close_current()
                self._key = None

    def _close_current(self) -> None:
        catalog = self._catalog
        self._catalog = None
        if catalog is None:
            return
        closer = getattr(catalog, "close", None)
        if not callable(closer):
            return
        try:
            closer()
        except Exception:  # noqa: BLE001 - closing must never block an update
            logger.warning("Falha ao fechar o catálogo anterior.", exc_info=True)

    def set_embedder(self, embedder: Embedder | None) -> None:
        """Replace the embedder and reset release resolution."""
        with self._lock:
            self.embedder = embedder
            if self.injected is not None:
                setter = getattr(self.injected, "set_embedder", None)
                if callable(setter):
                    setter(embedder)
            else:
                self._close_current()
                self._key = None


class AppState:
    """State attached to ``app.state.ctx``."""

    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        *,
        embedder: Embedder | None = None,
        catalog: Any | None = None,
        jobs: JobManager | None = None,
        quit_callback: Callable[[], Any] | None = None,
    ) -> None:
        self.paths = paths
        self.settings = settings
        self.jobs = jobs or JobManager()
        self.quit_callback = quit_callback
        self.installer = CatalogInstaller(paths, settings)
        self.embedder = embedder if embedder is not None else build_embedder(settings, paths)
        self.provider = CatalogProvider(paths, self.installer, self.embedder, catalog)
        self.catalog_injected = catalog is not None
        self.library = LibraryStore(paths)
        self.ingestor = LibraryIngestor(paths, self.embedder, store=self.library)
        self.remote = RemoteController(
            paths,
            settings,
            self.jobs,
            embedder=self.embedder,
            settings_saver=lambda new: save_remote_settings(paths, new),
        )
        self.installer.cleanup_stale()

    @property
    def catalog(self) -> Any | None:
        """Return the active catalog search controller."""
        return self.provider.get()

    def require_catalog(self) -> Any:
        catalog = self.catalog
        if catalog is None:
            raise SearchError("O catálogo ainda não foi instalado.")
        return catalog

    def model_status(self) -> dict[str, Any]:
        """Describe the configured embedding engine for /api/status."""
        engine = self.settings.embedding.engine
        ready = False
        if self.embedder is not None:
            try:
                ready = bool(self.embedder.ready())
            except Exception:  # noqa: BLE001 - optional external service
                ready = False
        if engine == "local":
            label = f"EmbeddingGemma 300M local ({self.settings.embedding.local_variant})"
        elif engine in {"openai", "openai_compatible"}:
            label = self.settings.embedding.model or "Servidor de embeddings"
        else:
            label = "Busca semântica desativada"
        return {
            "engine": engine,
            "ready": ready,
            "label": label,
            "downloading": self.jobs.is_active("setup"),
        }

    def catalog_status(self) -> dict[str, Any]:
        """Return installed catalog counters."""
        if self.catalog_injected:
            catalog = self.provider.injected
            stats = getattr(catalog, "stats", None)
            if callable(stats):
                try:
                    values = stats()
                    if isinstance(values, dict):
                        return {
                            "installed": bool(values.get("records")),
                            "version": values.get("version"),
                            "records": int(values.get("records") or 0),
                            "built_at": values.get("built_at"),
                            "years": values.get("years") or [],
                            "missing_vectors": int(values.get("missing_vectors") or 0),
                        }
                except Exception:  # noqa: BLE001 - injected test double
                    pass
        return self.installer.installed_status()

    def library_status(self) -> dict[str, int]:
        """Return document/chunk counters for ``/api/status``."""
        try:
            return self.library.counts()
        except Exception:  # noqa: BLE001 - status must never fail
            return {"documents": 0, "chunks": 0, "pending": 0}

    def update_settings(self, settings: Settings) -> None:
        """Apply new settings in-process without restarting the server."""
        self.settings = settings
        self.installer.settings = settings
        self.installer.clear_update_cache()
        self.embedder = build_embedder(settings, self.paths)
        self.provider.set_embedder(self.embedder)
        self.ingestor.set_embedder(self.embedder)
        self.remote.update_settings(settings)

    def release_dir(self) -> Path | None:
        """Return the current release directory, if installed."""
        return self.installer.current_release_dir()


__all__ = ["AppState", "CatalogProvider"]
