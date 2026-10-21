"""Resumable HTTP and local-directory downloads with integrity checks."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

import requests

CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 120.0
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
CHUNK_SIZE = 64 * 1024
RETRIES = 3
USER_AGENT = "BuscaTeses/1.0"

ProgressCallback = Callable[[int, int | None], None]

_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


class DownloadError(RuntimeError):
    """Raised when a download cannot be completed or fails verification."""


class SizeMismatchError(DownloadError):
    """Raised when a file's size differs from the expected byte count."""


class ChecksumMismatchError(DownloadError):
    """Raised when a file's SHA-256 differs from the expected digest."""


def hf_model_url(repo: str, revision: str, path: str) -> str:
    """Build a Hugging Face model file URL."""
    return f"https://huggingface.co/{repo}/resolve/{revision}/{path}"


def hf_dataset_url(repo: str, revision: str, path: str) -> str:
    """Build a Hugging Face dataset file URL."""
    return f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"


def hf_dataset_manifest_url(repo: str, revision: str = "main") -> str:
    """Build the manifest URL for a dataset revision."""
    return hf_dataset_url(repo, revision, "manifest.json")


def is_local_base(base: str | Path | None) -> bool:
    """Return whether ``base`` points to an existing local directory."""
    if base is None or str(base).strip() == "":
        return False
    parsed = urlparse(str(base))
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().is_dir()
    if parsed.scheme in {"http", "https"}:
        return False
    return Path(str(base)).expanduser().is_dir()


def local_base_path(base: str | Path) -> Path:
    """Return the directory represented by a local base URL or path."""
    parsed = urlparse(str(base))
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme in {"http", "https"}:
        raise DownloadError("A base informada não é um diretório local.")
    else:
        path = Path(str(base))
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise DownloadError(f"Diretório local não encontrado: {path}")
    return path


def dataset_manifest_source(
    base_url: str | None,
    repo: str,
    revision: str = "main",
) -> str:
    """Return a manifest location usable by :func:`fetch_dataset_file`."""
    if base_url and str(base_url).strip():
        base = str(base_url).strip()
        if is_local_base(base):
            return (local_base_path(base) / "manifest.json").as_uri()
        if base.startswith(("http://", "https://")):
            return f"{base.rstrip('/')}/manifest.json"
        raise DownloadError(f"Base do dataset inválida: {base}")
    return hf_dataset_manifest_url(repo, revision)


def dataset_file_source(
    base_url: str | None,
    repo: str,
    revision: str,
    relative_path: str,
) -> str:
    """Return a source URL/path for a dataset file."""
    _validate_relative_path(relative_path)
    if base_url and str(base_url).strip():
        base = str(base_url).strip()
        if is_local_base(base):
            root = local_base_path(base)
            return str(_safe_local_child(root, relative_path))
        if base.startswith(("http://", "https://")):
            return f"{base.rstrip('/')}/{relative_path}"
        raise DownloadError(f"Base do dataset inválida: {base}")
    return hf_dataset_url(repo, revision, relative_path)


def fetch_dataset_file(
    base_url: str | None,
    repo: str,
    revision: str,
    relative_path: str,
    destination: str | Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    progress: ProgressCallback | None = None,
    session: requests.Session | None = None,
) -> Path:
    """Fetch one dataset file from a local base, HTTP mirror or Hugging Face."""
    source = dataset_file_source(base_url, repo, revision, relative_path)
    if is_local_base(base_url):
        return copy_local_file(
            source,
            destination,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            progress=progress,
        )
    return download_file(
        source,
        destination,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
        progress=progress,
        session=session,
    )


def download_file(
    url: str,
    destination: str | Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    progress: ProgressCallback | None = None,
    retries: int = RETRIES,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """Download ``url`` to ``destination`` with resume and verification.

    A sibling ``.part`` file is reused through HTTP ``Range`` requests. On
    success the partial file is atomically renamed. Failures are retried up to
    ``retries`` times; integrity failures discard the partial file first.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    attempts = max(1, int(retries))
    own_session = session is None
    http = session or requests.Session()
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        request_headers.update(headers)

    try:
        if target.exists() and _verify_existing(
            target, expected_bytes, expected_sha256, progress
        ):
            return target
        if target.exists() and (expected_bytes is not None or expected_sha256):
            target.unlink(missing_ok=True)

        if partial.exists() and _matches_expectations(partial, expected_bytes, expected_sha256):
            _finalize(partial, target, expected_bytes, expected_sha256, progress)
            return target

        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                _download_once(
                    http,
                    url,
                    partial,
                    expected_bytes=expected_bytes,
                    progress=progress,
                    timeout=timeout,
                    headers=request_headers,
                    chunk_size=chunk_size,
                )
                _verify_or_raise(partial, expected_bytes, expected_sha256)
                if progress is not None:
                    progress(partial.stat().st_size, expected_bytes or partial.stat().st_size)
                os.replace(partial, target)
                return target
            except (requests.RequestException, OSError, DownloadError) as exc:
                last_error = exc
                if isinstance(exc, ChecksumMismatchError):
                    partial.unlink(missing_ok=True)
                elif isinstance(exc, SizeMismatchError) and (
                    expected_bytes is None
                    or not partial.exists()
                    or partial.stat().st_size >= expected_bytes
                ):
                    partial.unlink(missing_ok=True)
                if attempt < attempts:
                    time.sleep(min(2.0, 0.25 * attempt))
        if isinstance(last_error, DownloadError):
            raise last_error
        raise DownloadError(
            "Não foi possível baixar o arquivo. Verifique a conexão e tente novamente."
        ) from last_error
    finally:
        if own_session:
            http.close()


def copy_local_file(
    source: str | Path,
    destination: str | Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    progress: ProgressCallback | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """Copy a file from a local dataset mirror with the same verification."""
    origin = Path(source)
    if not origin.is_file():
        raise DownloadError(f"Arquivo não encontrado: {origin.name}")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and _verify_existing(target, expected_bytes, expected_sha256, progress):
        return target
    partial = target.with_name(f"{target.name}.part")
    partial.unlink(missing_ok=True)
    total = expected_bytes if expected_bytes is not None else origin.stat().st_size
    done = 0
    try:
        with origin.open("rb") as source_handle, partial.open("wb") as target_handle:
            while chunk := source_handle.read(chunk_size):
                target_handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        _verify_or_raise(partial, expected_bytes, expected_sha256)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def sha256_file(path: str | Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Compute the hexadecimal SHA-256 of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_size_and_hash(
    path: str | Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    """Raise :class:`DownloadError` if size or hash do not match."""
    _verify_or_raise(Path(path), expected_bytes, expected_sha256)


def _download_once(
    session: requests.Session,
    url: str,
    partial: Path,
    *,
    expected_bytes: int | None,
    progress: ProgressCallback | None,
    timeout: tuple[float, float],
    headers: dict[str, str],
    chunk_size: int,
) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    if expected_bytes is not None and offset > expected_bytes:
        partial.unlink(missing_ok=True)
        offset = 0
    request_headers = dict(headers)
    if offset:
        request_headers["Range"] = f"bytes={offset}-"

    response = session.get(
        url,
        stream=True,
        timeout=timeout,
        headers=request_headers,
        allow_redirects=True,
    )
    try:
        if response.status_code == 416 and expected_bytes is not None:
            if partial.exists() and partial.stat().st_size == expected_bytes:
                return
            partial.unlink(missing_ok=True)
            raise DownloadError("O servidor não aceitou a retomada. Baixando novamente.")
        response.raise_for_status()
        append = offset > 0 and response.status_code == 206
        if not append:
            offset = 0
        total = expected_bytes
        if total is None and response.status_code == 206:
            match = _CONTENT_RANGE.match(response.headers.get("Content-Range", ""))
            if match and match.group(3) != "*":
                total = int(match.group(3))
        if total is None:
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    total = offset + int(length)
                except ValueError:
                    total = None
        mode = "ab" if append else "wb"
        done = offset
        with partial.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        response.close()


def _verify_existing(
    path: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
    progress: ProgressCallback | None,
) -> bool:
    if expected_bytes is not None and path.stat().st_size != expected_bytes:
        return False
    if expected_sha256 and sha256_file(path).lower() != expected_sha256.lower():
        return False
    if progress is not None:
        size = path.stat().st_size
        progress(size, expected_bytes if expected_bytes is not None else size)
    return True


def _matches_expectations(
    path: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
) -> bool:
    if not path.exists():
        return False
    if expected_bytes is not None and path.stat().st_size != expected_bytes:
        return False
    if expected_sha256 is not None:
        return sha256_file(path).lower() == expected_sha256.lower()
    return expected_bytes is not None


def _finalize(
    partial: Path,
    target: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
    progress: ProgressCallback | None,
) -> None:
    _verify_or_raise(partial, expected_bytes, expected_sha256)
    if progress is not None:
        size = partial.stat().st_size
        progress(size, expected_bytes if expected_bytes is not None else size)
    os.replace(partial, target)


def _verify_or_raise(
    path: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
) -> None:
    if not path.is_file():
        raise DownloadError("O arquivo baixado não foi encontrado.")
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise SizeMismatchError(
            f"O tamanho do arquivo não confere (esperado {expected_bytes}, obtido {size})."
        )
    if expected_sha256:
        actual = sha256_file(path)
        if actual.lower() != expected_sha256.lower():
            raise ChecksumMismatchError(
                "A verificação de integridade (sha256) não confere. O arquivo será baixado novamente."
            )


def _validate_relative_path(relative_path: str) -> None:
    candidate = PurePosixPath(str(relative_path).replace("\\", "/"))
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise DownloadError("Caminho de arquivo do dataset inválido.")


def _safe_local_child(root: Path, relative_path: str) -> Path:
    child = (root / Path(relative_path)).resolve()
    try:
        child.relative_to(root.resolve())
    except ValueError as exc:
        raise DownloadError("Caminho de arquivo do dataset inválido.") from exc
    return child


__all__ = [
    "CONNECT_TIMEOUT",
    "ChecksumMismatchError",
    "DownloadError",
    "SizeMismatchError",
    "READ_TIMEOUT",
    "RETRIES",
    "copy_local_file",
    "dataset_file_source",
    "dataset_manifest_source",
    "download_file",
    "fetch_dataset_file",
    "hf_dataset_manifest_url",
    "hf_dataset_url",
    "hf_model_url",
    "is_local_base",
    "local_base_path",
    "sha256_file",
    "verify_file_size_and_hash",
]
