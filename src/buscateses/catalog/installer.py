"""Install and update the local catalog release (SPEC §4)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import requests
from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from buscateses import __version__
from buscateses.catalog.builder import (
    CATALOG_DB,
    FORMAT_VERSION,
    MANIFEST_FILE,
    VECTORS_FILE,
    build_catalog,
    write_build_marker,
)
from buscateses.catalog.manifest import Manifest, ManifestError, load_manifest, parse_manifest
from buscateses.config import Paths, Settings, get_paths, get_settings
from buscateses.downloads import (
    DEFAULT_TIMEOUT,
    DownloadError,
    fetch_dataset_file,
    hf_dataset_manifest_url,
    is_local_base,
    local_base_path,
    verify_file_size_and_hash,
)

UPDATE_CACHE_SECONDS = 6 * 60 * 60
SPACE_MULTIPLIER = 2.5
#: Build/backup folders younger than this are always kept at startup, so a
#: concurrent installer in another process is never interrupted.
STALE_BUILD_SECONDS = 6 * 60 * 60
_INSTALL_BUSY_MESSAGE = (
    "Já existe uma instalação ou atualização do catálogo em andamento. "
    "Aguarde o término e tente novamente."
)

logger = logging.getLogger(__name__)


class CatalogError(RuntimeError):
    """Raised for catalog update/install failures with user-facing messages."""


@dataclass(frozen=True)
class UpdateInfo:
    """Result of checking the remote dataset manifest."""

    available: bool
    current: str | None
    latest: str
    records_delta: int
    download_bytes: int
    manifest: Manifest

    def to_dict(self) -> dict[str, Any]:
        """Return the public API representation (without the full manifest)."""
        return {
            "available": self.available,
            "current": self.current,
            "latest": self.latest,
            "records_delta": self.records_delta,
            "download_bytes": self.download_bytes,
        }


class CatalogInstaller:
    """Download, verify, build and atomically activate catalog releases."""

    def __init__(
        self,
        paths: Paths | None = None,
        settings: Settings | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.paths = paths or get_paths()
        self.settings = settings or get_settings(self.paths)
        self._session = session or requests.Session()
        self._own_session = session is None
        self._cache: tuple[float, UpdateInfo] | None = None
        self._manifest_cache: dict[str, Manifest] = {}

    def clear_update_cache(self) -> None:
        """Invalidate the in-memory update cache."""
        self._cache = None
        self._manifest_cache.clear()

    def check_update(self, *, force: bool = False) -> UpdateInfo:
        """Check the remote manifest, caching the result for six hours."""
        now = time.monotonic()
        if not force and self._cache is not None:
            cached_at, cached = self._cache
            if now - cached_at < UPDATE_CACHE_SECONDS:
                return cached
        manifest = self._fetch_manifest("main")
        self._check_min_version(manifest)
        current = self.current_version()
        current_records = self._release_records(current) if current else 0
        # Format-1 releases keep working, but a rebuild is preferred on the
        # next update so users get compressed abstracts and float16 vectors.
        outdated_format = current is not None and self._current_format_version() < FORMAT_VERSION
        # A reinstall of the same release uses a ``<version>+fN`` directory, so
        # compare only the base version with the manifest.
        info = UpdateInfo(
            available=_base_version(current) != manifest.version or outdated_format,
            current=current,
            latest=manifest.version,
            records_delta=manifest.records - current_records,
            download_bytes=manifest.download_bytes,
            manifest=manifest,
        )
        self._cache = (now, info)
        self._manifest_cache[manifest.version] = manifest
        return info

    def current_state(self) -> dict[str, Any] | None:
        """Return the parsed ``current.json`` or ``None`` when invalid/missing."""
        path = self.paths.current_file
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        directory = data.get("dir")
        if not isinstance(version, str) or not isinstance(directory, str):
            return None
        release = self.paths.release_dir(version)
        if release != (self.paths.catalog_dir / directory).resolve():
            return None
        return {"version": version, "dir": directory}

    def current_version(self) -> str | None:
        """Return the active catalog version, if any."""
        state = self.current_state()
        return state["version"] if state else None

    def current_release_dir(self) -> Path | None:
        """Return the active release directory when it looks complete."""
        state = self.current_state()
        if not state:
            return None
        directory = self.paths.release_dir(state["version"])
        if not (directory / CATALOG_DB).is_file():
            return None
        return directory

    def installed_status(self) -> dict[str, Any]:
        """Return compact status information about the installed catalog."""
        state = self.current_state()
        directory = self.current_release_dir()
        if not state or directory is None:
            return {
                "installed": False,
                "version": None,
                "records": 0,
                "built_at": None,
                "years": [],
                "missing_vectors": 0,
            }
        values = self._read_meta(directory)
        years: list[int] = []
        raw_years = values.get("years", "")
        if "," in raw_years:
            try:
                years = [int(part) for part in raw_years.split(",", 1)]
            except ValueError:
                years = []
        return {
            "installed": True,
            "version": state["version"],
            "records": _safe_int(values.get("records"), 0),
            "built_at": values.get("built_at"),
            "years": years,
            "missing_vectors": _safe_int(values.get("missing_vectors"), 0),
        }

    def install(
        self,
        version: str | None = None,
        *,
        ctx: Any | None = None,
        before_activate: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Install ``version`` (or the latest manifest) and activate it atomically.

        A cross-process file lock serialises ``setup``/``update`` runs from the
        CLI and the API, so two installers never build or swap releases at the
        same time. ``before_activate`` runs just before the active release is
        swapped, so the caller can close open files (required on Windows).
        """
        with self._install_lock():
            return self._install(
                version,
                ctx=ctx,
                before_activate=before_activate,
            )

    @contextmanager
    def _install_lock(self) -> Iterator[None]:
        """Hold ``catalog/.install.lock`` for the whole install operation."""
        self.paths.ensure()
        lock = FileLock(str(self.paths.install_lock), timeout=0)
        try:
            lock.acquire()
        except FileLockTimeout as exc:
            raise CatalogError(_INSTALL_BUSY_MESSAGE) from exc
        try:
            yield
        finally:
            lock.release()

    def _install(
        self,
        version: str | None = None,
        *,
        ctx: Any | None = None,
        before_activate: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Build and activate a release; callers must hold the install lock."""
        manifest = self._manifest_for_install(version)
        self._check_min_version(manifest)
        target_version = manifest.version
        storage_version = self._storage_version(target_version)
        releases = self.paths.releases_dir
        downloads = self.paths.download_dir(target_version)
        final_release = self.paths.release_dir(storage_version)
        work_release = releases / f".build-{storage_version}-{uuid.uuid4().hex[:8]}"
        backup_release = releases / f".old-{storage_version}-{uuid.uuid4().hex[:8]}"
        self.paths.ensure()
        downloads.mkdir(parents=True, exist_ok=True)

        install_files = manifest.files_for_install()
        total_bytes = sum(item.bytes for item in install_files)
        free = shutil.disk_usage(downloads).free
        if free < int(total_bytes * SPACE_MULTIPLIER):
            raise CatalogError(
                "Não há espaço livre suficiente para instalar o catálogo. "
                "Libere espaço no disco e tente novamente."
            )

        old_state = self.current_state()
        final_moved = False
        current_written = False
        try:
            self._download(
                manifest,
                downloads,
                total_bytes=total_bytes,
                ctx=ctx,
            )
            self._verify_downloads(manifest, downloads, total_bytes=total_bytes, ctx=ctx)
            _check_cancelled(ctx)
            if ctx is not None:
                ctx.update(
                    stage="build_db",
                    message="Montando o catálogo local…",
                    progress=0.0,
                    bytes_done=0,
                    bytes_total=0,
                )
            result = build_catalog(manifest, downloads, work_release, ctx=ctx)
            _check_cancelled(ctx)
            self._validate_release(work_release, target_version)
            write_build_marker(
                work_release,
                {
                    "version": target_version,
                    "records": result.records,
                    "missing_vectors": result.missing_vectors,
                    "built_at": result.built_at,
                },
            )
            if before_activate is not None:
                before_activate()
            if final_release.exists():
                os.replace(final_release, backup_release)
            os.replace(work_release, final_release)
            final_moved = True
            self._write_current(storage_version)
            current_written = True
            if ctx is not None:
                ctx.update(
                    stage="finalize",
                    message="Ativando o novo catálogo…",
                    progress=1.0,
                )
            self._cleanup(
                keep_versions={
                    storage_version,
                    old_state["version"] if old_state else storage_version,
                }
            )
            _remove_tree(downloads)
            if backup_release.exists():
                _remove_tree(backup_release)
            return {
                "version": target_version,
                "dir": f"releases/{storage_version}",
                "records": result.records,
                "missing_vectors": result.missing_vectors,
                "built_at": result.built_at,
            }
        except BaseException:
            _remove_tree(work_release)
            if current_written and old_state:
                self._write_current(old_state["version"], directory=old_state["dir"])
            elif current_written:
                self.paths.current_file.unlink(missing_ok=True)
            if final_moved and backup_release.exists():
                _remove_tree(final_release)
                os.replace(backup_release, final_release)
            elif backup_release.exists() and not final_release.exists():
                os.replace(backup_release, final_release)
            raise

    def _manifest_for_install(self, version: str | None) -> Manifest:
        if version is None:
            return self.check_update().manifest
        cached = self._manifest_cache.get(version)
        if cached is not None:
            return cached
        manifest = self._fetch_manifest(version)
        if manifest.version != version:
            raise CatalogError(
                "A versão do catálogo solicitada não corresponde ao manifest disponível."
            )
        self._manifest_cache[version] = manifest
        return manifest

    def _fetch_manifest(self, revision: str) -> Manifest:
        base = self.settings.dataset_url
        try:
            if base and is_local_base(base):
                path = local_base_path(base) / "manifest.json"
                return load_manifest(path)
            if base and base.startswith(("http://", "https://")):
                if revision in {"main", "master"}:
                    url = f"{base.rstrip('/')}/manifest.json"
                else:
                    url = f"{base.rstrip('/')}/{revision}/manifest.json"
            else:
                target = "main" if revision == "main" else revision
                url = hf_dataset_manifest_url(self.settings.dataset_repo, target)
            response = self._session.get(url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return parse_manifest(response.content)
        except (requests.RequestException, OSError, ManifestError) as exc:
            raise CatalogError(
                "Não foi possível verificar o catálogo na internet. "
                "Tente novamente mais tarde."
            ) from exc

    def _download(
        self,
        manifest: Manifest,
        downloads: Path,
        *,
        total_bytes: int,
        ctx: Any | None,
    ) -> None:
        completed = 0
        if ctx is not None:
            ctx.update(
                stage="download",
                message="Baixando o catálogo…",
                progress=0.0,
                bytes_done=0,
                bytes_total=total_bytes,
            )
        for item in manifest.files_for_install():
            _check_cancelled(ctx)
            destination = downloads / item.path
            base = completed

            def progress(
                done: int,
                _total: int | None,
                *,
                base: int = base,
                item_bytes: int = item.bytes,
            ) -> None:
                if ctx is None:
                    return
                overall = base + min(done, item_bytes)
                ctx.update(
                    stage="download",
                    message="Baixando o catálogo…",
                    bytes_done=overall,
                    bytes_total=total_bytes,
                    progress=(overall / total_bytes) if total_bytes else 1.0,
                )

            try:
                fetch_dataset_file(
                    self.settings.dataset_url,
                    self.settings.dataset_repo,
                    manifest.revision,
                    item.path,
                    destination,
                    expected_bytes=item.bytes,
                    expected_sha256=item.sha256,
                    progress=progress,
                    session=self._session,
                )
            except DownloadError as exc:
                raise CatalogError(
                    "Não foi possível baixar o catálogo. Verifique a conexão e tente novamente."
                ) from exc
            completed += item.bytes

    def _verify_downloads(
        self,
        manifest: Manifest,
        downloads: Path,
        *,
        total_bytes: int,
        ctx: Any | None,
    ) -> None:
        if ctx is not None:
            ctx.update(
                stage="verify",
                message="Verificando os arquivos do catálogo…",
                progress=0.0,
                bytes_done=0,
                bytes_total=total_bytes,
            )
        verified = 0
        for item in manifest.files_for_install():
            _check_cancelled(ctx)
            destination = downloads / item.path
            try:
                verify_file_size_and_hash(
                    destination,
                    expected_bytes=item.bytes,
                    expected_sha256=item.sha256,
                )
            except DownloadError as exc:
                raise CatalogError(
                    f"O arquivo {item.path} está corrompido. Tente baixar novamente."
                ) from exc
            verified += item.bytes
            if ctx is not None:
                ctx.update(
                    stage="verify",
                    message="Verificando os arquivos do catálogo…",
                    progress=(verified / total_bytes) if total_bytes else 1.0,
                    bytes_done=verified,
                    bytes_total=total_bytes,
                )

    def _validate_release(self, release: Path, version: str) -> None:
        database = release / CATALOG_DB
        vectors = release / VECTORS_FILE
        if (
            not database.is_file()
            or not vectors.is_file()
            or not (release / MANIFEST_FILE).is_file()
        ):
            raise CatalogError("O catálogo gerado está incompleto.")
        meta = self._read_meta(release)
        if meta.get("version") != version:
            raise CatalogError("A versão do catálogo gerado não confere.")
        try:
            loaded = np.load(vectors, mmap_mode="r")
            if (
                loaded.ndim != 2
                or loaded.shape[1] != 256
                or loaded.dtype not in (np.dtype(np.float16), np.dtype(np.float32))
            ):
                raise CatalogError("Os vetores do catálogo gerado são inválidos.")
        except (OSError, ValueError) as exc:
            raise CatalogError("Os vetores do catálogo gerado são inválidos.") from exc

    def _read_meta(self, release: Path) -> dict[str, str]:
        database = release / CATALOG_DB
        if not database.is_file():
            return {}
        try:
            connection = sqlite3.connect(
                f"file:{quote(str(database))}?mode=ro", uri=True
            )
            try:
                rows = connection.execute("SELECT key, value FROM meta").fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            return {}
        return {str(key): str(value) for key, value in rows}

    def _release_records(self, version: str) -> int:
        try:
            directory = self.paths.release_dir(version)
        except ValueError:
            return 0
        return _safe_int(self._read_meta(directory).get("records"), 0)

    def _current_format_version(self) -> int:
        """Return the active release's format version (1 for legacy builds)."""
        directory = self.current_release_dir()
        if directory is None:
            return 0
        return _safe_int(self._read_meta(directory).get("format_version"), 1)

    def _write_current(self, version: str, *, directory: str | None = None) -> None:
        self.paths.ensure()
        payload = {
            "version": version,
            "dir": directory or f"releases/{version}",
        }
        destination = self.paths.current_file
        descriptor, temporary = tempfile.mkstemp(
            prefix=".current.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _storage_version(self, target_version: str) -> str:
        """Pick a release directory, never reusing one from the same version.

        Rebuilding the same version (for example after a format change) gets a
        distinct ``<version>+f<format>`` directory so the previous release can
        be closed and removed safely.
        """
        if not self.paths.release_dir(target_version).exists():
            return target_version
        candidate = f"{target_version}+f{FORMAT_VERSION}"
        index = 2
        while self.paths.release_dir(candidate).exists():
            candidate = f"{target_version}+f{FORMAT_VERSION}-{index}"
            index += 1
        return candidate

    def cleanup_stale(self) -> None:
        """Remove leftover build/backup directories and unreferenced releases.

        Build and backup folders are only removed when no install is running
        and they are at least six hours old. This protects a concurrent
        ``setup``/``update`` (which holds ``catalog/.install.lock``) from being
        deleted by another app instance starting at the same time.
        Failures are logged and retried on the next startup instead of aborting
        the application.
        """
        if not self.paths.releases_dir.is_dir():
            return
        state = self.current_state()
        keep = {state["version"]} if state else set()
        install_busy = self._install_lock_busy()
        now = time.time()
        for path in self.paths.releases_dir.iterdir():
            if path.name in keep:
                continue
            if path.name.startswith((".build-", ".old-")):
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                if install_busy or age < STALE_BUILD_SECONDS:
                    continue
            _remove_tree(path)

    def _install_lock_busy(self) -> bool:
        """Return whether another process currently holds the install lock."""
        self.paths.ensure()
        lock = FileLock(str(self.paths.install_lock), timeout=0)
        try:
            lock.acquire()
        except FileLockTimeout:
            return True
        except OSError:
            # If the lock cannot even be created, err on the safe side.
            return True
        lock.release()
        return False

    def _cleanup(self, *, keep_versions: set[str]) -> None:
        for path in self.paths.releases_dir.iterdir():
            if path.name in keep_versions:
                continue
            _remove_tree(path)

    def _check_min_version(self, manifest: Manifest) -> None:
        if _version_tuple(manifest.min_app_version) > _version_tuple(__version__):
            raise CatalogError(
                "Esta versão do catálogo exige uma versão mais nova do BuscaTeses."
            )


def check_update(
    paths: Paths | None = None,
    settings: Settings | None = None,
    *,
    force: bool = False,
    session: requests.Session | None = None,
) -> UpdateInfo:
    """Convenience wrapper around :meth:`CatalogInstaller.check_update`."""
    return CatalogInstaller(paths, settings, session=session).check_update(force=force)


def install_catalog(
    paths: Paths | None = None,
    settings: Settings | None = None,
    version: str | None = None,
    *,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Convenience wrapper around :meth:`CatalogInstaller.install`."""
    return CatalogInstaller(paths, settings).install(version, ctx=ctx)


def _check_cancelled(ctx: Any | None) -> None:
    if ctx is not None:
        ctx.check_cancelled()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _base_version(value: str | None) -> str | None:
    """Strip the ``+fN`` storage suffix from a release version."""
    if value is None:
        return None
    return str(value).split("+", 1)[0]


def _remove_tree(path: Path) -> None:
    """Delete a directory, logging failures so a later startup can retry."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning("Não foi possível remover %s: %s", path, exc)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(value).split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


__all__ = [
    "CatalogError",
    "CatalogInstaller",
    "SPACE_MULTIPLIER",
    "UPDATE_CACHE_SECONDS",
    "UpdateInfo",
    "check_update",
    "install_catalog",
]
