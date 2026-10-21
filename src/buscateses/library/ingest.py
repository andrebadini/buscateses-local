"""Serial library ingestion pipeline (SPEC §7)."""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from buscateses.config import Paths
from buscateses.embeddings.base import Embedder
from buscateses.jobs import JobCancelled, JobManager
from buscateses.library.chunking import TextChunk, chunk_pages
from buscateses.library.extract import (
    SUPPORTED_EXTENSIONS,
    ExtractionError,
    extract_document,
    humanize_filename_title,
    meaningful_title,
)
from buscateses.library.store import (
    MAX_FILE_BYTES,
    LibraryError,
    LibraryStore,
    safe_filename,
    sha256_file,
)

EMBED_BATCH_SIZE = 16
_COPY_BLOCK = 1024 * 1024


@dataclass
class IngestOutcome:
    """Result of ingesting one file."""

    doc_id: str
    filename: str
    title: str | None
    status: str
    message: str
    skipped: bool = False
    warnings: list[str] = field(default_factory=list)


class LibraryIngestor:
    """Extract, chunk, embed and persist documents one at a time."""

    def __init__(
        self,
        paths: Paths,
        embedder: Embedder | None = None,
        *,
        store: LibraryStore | None = None,
    ) -> None:
        self.paths = paths
        self.embedder = embedder
        self.store = store or LibraryStore(paths)
        self._lock = threading.Lock()

    def set_embedder(self, embedder: Embedder | None) -> None:
        """Replace the embedder used for future ingestions."""
        self.embedder = embedder

    def submit(
        self,
        jobs: JobManager,
        source: str | Path,
        *,
        filename: str | None = None,
        remove_source: bool = False,
    ) -> str:
        """Submit one ingestion job, returning its id."""
        path = Path(source)
        display_name = safe_filename(filename or path.name)

        def run(ctx: Any) -> None:
            try:
                outcome = self.ingest_path(
                    path,
                    filename=display_name,
                    ctx=ctx,
                    move_source=remove_source,
                )
                ctx.update(stage="done", message=outcome.message, progress=1.0)
            finally:
                if remove_source:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

        return jobs.submit("library", run)

    def ingest_path(
        self,
        source: str | Path,
        *,
        filename: str | None = None,
        ctx: Any | None = None,
        move_source: bool = False,
    ) -> IngestOutcome:
        """Ingest ``source``. Serialised across threads by an internal lock.

        When ``move_source`` is true the original file is moved into the
        library instead of copied; this avoids duplicating multi-hundred
        megabyte uploads. The move falls back to a copy across filesystems.
        """
        path = Path(source)
        display_name = safe_filename(filename or path.name)
        with self._lock:
            return self._ingest_locked(path, display_name, ctx, move_source=move_source)

    def _ingest_locked(
        self,
        source: Path,
        filename: str,
        ctx: Any | None,
        *,
        move_source: bool = False,
    ) -> IngestOutcome:
        if not source.is_file():
            raise LibraryError("O arquivo não foi encontrado.")
        kind = SUPPORTED_EXTENSIONS.get(Path(filename).suffix.lower())
        if kind is None:
            kind = SUPPORTED_EXTENSIONS.get(source.suffix.lower())
        if kind is None:
            raise LibraryError(
                "Formato não suportado. Envie PDF, EPUB, DOCX, TXT, Markdown ou HTML."
            )
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            raise LibraryError("O arquivo é maior que o limite de 500 MB.")
        _update(ctx, stage="verify", message=f"Verificando «{filename}»…", progress=0.02)
        digest = sha256_file(source)
        existing = self.store.find_by_sha256(digest)
        if existing is not None:
            return IngestOutcome(
                doc_id=str(existing["id"]),
                filename=filename,
                title=existing.get("title"),
                status=str(existing.get("status") or "ready"),
                message=f"«{filename}» já está na biblioteca.",
                skipped=True,
            )

        doc_id = uuid.uuid4().hex
        added_at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            self.store.create_document(
                doc_id=doc_id,
                sha256=digest,
                filename=filename,
                kind=kind,
                added_at=added_at,
                status="processing",
            )
        except LibraryError:
            existing = self.store.find_by_sha256(digest)
            if existing is None:
                raise
            return IngestOutcome(
                doc_id=str(existing["id"]),
                filename=filename,
                title=existing.get("title"),
                status=str(existing.get("status") or "ready"),
                message=f"«{filename}» já está na biblioteca.",
                skipped=True,
            )
        destination = self.store.file_path_for(digest, kind)
        try:
            self._copy(
                source,
                destination,
                size=size,
                ctx=ctx,
                move_source=move_source,
            )
            _update(
                ctx,
                stage="extract",
                message=f"Extraindo texto de «{filename}»…",
                progress=0.15,
            )
            extracted = extract_document(destination, kind=kind)
            title = _display_title(extracted.title, filename, destination)
            if extracted.needs_ocr:
                message = (
                    "O PDF não tem texto selecionável. O reconhecimento de texto (OCR) "
                    "ainda não é oferecido nesta versão."
                )
                self.store.set_status(doc_id, "needs_ocr", error=message)
                self.store.update_document(
                    doc_id,
                    title=title,
                    author=extracted.author,
                    year=extracted.year,
                )
                return IngestOutcome(
                    doc_id=doc_id,
                    filename=filename,
                    title=title,
                    status="needs_ocr",
                    message=f"«{filename}» precisa de OCR.",
                )
            _update(
                ctx,
                stage="chunk",
                message="Dividindo o texto em trechos…",
                progress=0.3,
            )
            chunks = chunk_pages(extracted.pages)
            if not chunks:
                self.store.set_status(doc_id, "error", error="O documento não contém texto.")
                raise LibraryError("O documento não contém texto legível.")
            rowids = self.store.replace_chunks(doc_id, chunks)
            embedded = False
            warnings: list[str] = []
            if chunks and self._embedder_ready():
                try:
                    self._embed_chunks(
                        doc_id,
                        title,
                        chunks,
                        rowids,
                        ctx=ctx,
                    )
                    embedded = True
                except JobCancelled:
                    raise
                except Exception:  # noqa: BLE001 - ingestion must degrade gracefully
                    warnings.append("semantic_unavailable")
            else:
                warnings.append("semantic_unavailable")
            self.store.finish_document(
                doc_id,
                title=title,
                author=extracted.author,
                year=extracted.year,
                pages=len(extracted.pages),
                chars=extracted.chars,
                chunks=len(chunks),
                embedded=embedded,
            )
            _update(ctx, stage="done", progress=1.0)
            message = f"«{title}» adicionada à biblioteca."
            if not embedded:
                message += " Busca apenas textual até o modelo de embedding estar pronto."
            return IngestOutcome(
                doc_id=doc_id,
                filename=filename,
                title=title,
                status="ready",
                message=message,
                warnings=warnings,
            )
        except JobCancelled:
            self._cleanup_failed(doc_id, destination)
            raise
        except Exception as exc:
            message = _friendly_error(exc)
            self.store.set_status(doc_id, "error", error=message)
            raise LibraryError(message) from exc

    def _copy(
        self,
        source: Path,
        destination: Path,
        *,
        size: int,
        ctx: Any | None,
        move_source: bool = False,
    ) -> None:
        if move_source and self._move(source, destination, size=size, ctx=ctx):
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        copied = 0
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as handle:
                while block := handle.read(_COPY_BLOCK):
                    if ctx is not None:
                        ctx.check_cancelled()
                    output.write(block)
                    copied += len(block)
                    _update(
                        ctx,
                        stage="copy",
                        message="Copiando o arquivo…",
                        progress=0.02 + 0.1 * (copied / size if size else 1.0),
                        bytes_done=copied,
                        bytes_total=size,
                    )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _move(
        self,
        source: Path,
        destination: Path,
        *,
        size: int,
        ctx: Any | None,
    ) -> bool:
        """Try to move ``source`` into place; return False across filesystems."""
        if ctx is not None:
            ctx.check_cancelled()
        try:
            os.replace(source, destination)
        except OSError:
            return False
        _update(
            ctx,
            stage="copy",
            message="Movendo o arquivo…",
            progress=0.12,
            bytes_done=size,
            bytes_total=size,
        )
        return True

    def _embedder_ready(self) -> bool:
        if self.embedder is None:
            return False
        try:
            return bool(self.embedder.ready())
        except Exception:  # noqa: BLE001 - optional engine
            return False

    def _embed_chunks(
        self,
        doc_id: str,
        title: str,
        chunks: Sequence[TextChunk],
        rowids: Sequence[int],
        *,
        ctx: Any | None,
    ) -> None:
        assert self.embedder is not None
        vectors: list[np.ndarray] = []
        total = len(chunks)
        for start in range(0, total, EMBED_BATCH_SIZE):
            if ctx is not None:
                ctx.check_cancelled()
            batch = chunks[start : start + EMBED_BATCH_SIZE]
            matrix = np.asarray(
                self.embedder.embed_documents([(title, chunk.text) for chunk in batch]),
                dtype=np.float32,
            )
            if matrix.ndim != 2 or matrix.shape[1] != 768:
                raise LibraryError(
                    "O modelo de embedding retornou vetores incompatíveis (esperado: 768)."
                )
            vectors.append(matrix)
            done = min(start + len(batch), total)
            _update(
                ctx,
                stage="embed",
                message=f"Gerando vetores ({done}/{total})…",
                progress=0.4 + 0.5 * (done / total if total else 1.0),
            )
        if vectors:
            self.store.add_vectors(rowids, np.vstack(vectors))

    def _cleanup_failed(self, doc_id: str, destination: Path) -> None:
        try:
            self.store.delete_document(doc_id)
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass


def reindex_missing(
    store: LibraryStore,
    embedder: Embedder | None,
    *,
    ctx: Any | None = None,
) -> int:
    """Embed chunks that were ingested while no embedder was available."""
    if embedder is None:
        raise LibraryError(
            "O modelo de embedding ainda não está disponível. "
            "Baixe o modelo nas configurações do BuscaTeses e tente novamente."
        )
    try:
        ready = bool(embedder.ready())
    except Exception:  # noqa: BLE001 - optional engine
        ready = False
    if not ready:
        raise LibraryError(
            "O modelo de embedding ainda não está pronto. "
            "Baixe o modelo nas configurações do BuscaTeses e tente novamente."
        )
    missing = store.missing_vector_chunks()
    if not missing:
        if ctx is not None:
            ctx.update(stage="done", message="Todos os trechos já têm vetores.", progress=1.0)
        return 0
    vectors: list[np.ndarray] = []
    rowids: list[int] = []
    total = len(missing)
    for start in range(0, total, EMBED_BATCH_SIZE):
        if ctx is not None:
            ctx.check_cancelled()
        batch = missing[start : start + EMBED_BATCH_SIZE]
        matrix = np.asarray(
            embedder.embed_documents([(title, text) for _rowid, _doc, title, text in batch]),
            dtype=np.float32,
        )
        if matrix.ndim != 2 or matrix.shape[1] != 768:
            raise LibraryError("O modelo de embedding retornou vetores incompatíveis.")
        vectors.append(matrix)
        rowids.extend(rowid for rowid, _doc, _title, _text in batch)
        done = min(start + len(batch), total)
        if ctx is not None:
            ctx.update(
                stage="embed",
                message=f"Gerando vetores ({done}/{total})…",
                progress=done / total if total else 1.0,
            )
    store.add_vectors(rowids, np.vstack(vectors))
    store.mark_ready_embedded()
    return total


def _display_title(title: str | None, filename: str, stored: Path) -> str:
    """Prefer a meaningful metadata title, falling back to the file name."""
    clean = meaningful_title(title)
    stored_title = humanize_filename_title(stored.stem)
    if not clean or clean in {stored.stem, stored_title, str(stored)}:
        return humanize_filename_title(Path(filename).name) or Path(filename).stem
    return clean


def _update(ctx: Any | None, **values: Any) -> None:
    if ctx is not None:
        ctx.update(**values)


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, (LibraryError, ExtractionError)):
        return str(exc)
    text = str(exc).strip()
    if not text:
        return "Não foi possível adicionar este documento. Tente novamente."
    return f"Não foi possível adicionar este documento: {text}"


__all__ = [
    "EMBED_BATCH_SIZE",
    "IngestOutcome",
    "LibraryIngestor",
    "reindex_missing",
]
