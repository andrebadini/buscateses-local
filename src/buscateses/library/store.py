"""SQLite/FTS5 storage and hybrid search for the personal library (SPEC §7)."""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from buscateses.catalog.search import SearchError, parse_fts_query
from buscateses.config import Paths
from buscateses.embeddings.base import Embedder, truncate_normalize
from buscateses.fts_relax import (
    create_vocabulary,
    document_frequencies,
    relaxation_drops,
)
from buscateses.library.chunking import TextChunk

VECTOR_DIMS = 768
RRF_K = 60
SEMANTIC_CANDIDATES = 200
TEXT_CANDIDATES = 200
MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_RESULTS_PER_DOCUMENT = 3
SNIPPET_CHARS = 320
DOCUMENT_STATUSES = {"queued", "processing", "ready", "error", "needs_ocr"}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # User-supplied HTML/Markdown is never rendered: it is served as plain
    # text so a same-origin document cannot execute scripts against the API.
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "html": "text/plain; charset=utf-8",
}
#: Kinds that browsers must download instead of rendering inline.
ATTACHMENT_KINDS = frozenset({"epub", "docx"})
_SAFE_NAME_RE = re.compile(r"[\x00-\x1f\x7f]")


class LibraryError(RuntimeError):
    """Raised for library storage problems with user-facing messages."""


class LibraryNotFoundError(LibraryError):
    """Raised when a document, chunk or stored file does not exist."""


def safe_filename(value: str | None) -> str:
    """Return a harmless basename for an uploaded file."""
    name = Path(str(value or "").replace("\\", "/")).name
    name = _SAFE_NAME_RE.sub("", name).strip().strip(".")
    if not name:
        return "documento"
    if len(name) > 180:
        stem, suffix = os.path.splitext(name)
        name = stem[: max(1, 180 - len(suffix))] + suffix
    return name


def media_type_for(kind: str) -> str:
    """Return the HTTP media type for a document kind."""
    return MEDIA_TYPES.get(str(kind).lower(), "application/octet-stream")


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA-256 of ``path`` without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


class LibraryStore:
    """Thread-safe store backed by ``library.sqlite`` and numpy vectors.

    With ``read_only=True`` the database is opened in SQLite read-only mode and
    no schema or file is created; this is used by the MCP server.
    """

    def __init__(
        self,
        paths: Paths | str | Path,
        *,
        read_only: bool = False,
        lock_timeout: float = 30.0,
    ) -> None:
        if isinstance(paths, Paths):
            self.paths = paths
            self.database_path = paths.library_db
            self.vectors_path = paths.library_vectors
            self.vector_ids_path = paths.library_vector_ids
            self.files_dir = paths.library_files_dir
            lock_path = paths.library_lock
        else:
            base = Path(paths)
            self.paths = None
            self.database_path = base / "library.sqlite"
            self.vectors_path = base / "vectors.npy"
            self.vector_ids_path = base / "vector_rowids.npy"
            self.files_dir = base / "files"
            lock_path = base / ".write.lock"
        self.read_only = read_only
        self.available = self.database_path.is_file()
        self._lock = threading.RLock()
        self._write_lock = (
            None if read_only else FileLock(str(lock_path), timeout=max(0.0, lock_timeout))
        )
        if not read_only:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.files_dir.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self.available = True

    @contextmanager
    def _write_guard(self) -> Iterator[None]:
        """Serialise writes across threads and processes (server + CLI)."""
        if self._write_lock is None:
            yield
            return
        try:
            with self._write_lock:
                yield
        except FileLockTimeout as exc:
            raise LibraryError(
                "A biblioteca está sendo usada por outro programa. Tente novamente."
            ) from exc

    # -- connections and schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            uri = f"file:{quote(str(self.database_path))}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=15.0)
        else:
            connection = sqlite3.connect(self.database_path, timeout=15.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        create_vocabulary(connection, "chunks_fts")
        return connection

    def _require_available(self) -> None:
        if not self.available and not self.database_path.is_file():
            raise LibraryError(
                "A biblioteca ainda está vazia. Adicione documentos no app BuscaTeses."
            )

    def _initialize(self) -> None:
        with self._lock, self._write_guard(), self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    title TEXT,
                    author TEXT,
                    year INTEGER,
                    kind TEXT NOT NULL,
                    pages INTEGER NOT NULL DEFAULT 0,
                    chars INTEGER NOT NULL DEFAULT 0,
                    chunks INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT,
                    added_at TEXT NOT NULL,
                    embedded INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chunks(
                    rowid INTEGER PRIMARY KEY,
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ord INTEGER NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_doc_ord ON chunks(doc_id, ord);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text,
                    content='chunks',
                    content_rowid='rowid',
                    tokenize="unicode61 remove_diacritics 2"
                );
                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, text)
                    VALUES ('delete', old.rowid, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, text)
                    VALUES ('delete', old.rowid, old.text);
                    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
                """
            )

    # -- document CRUD ----------------------------------------------------------

    def find_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        """Return the document with ``sha256``, if any."""
        self._require_available()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE sha256 = ?", [str(sha256)]
            ).fetchone()
        return _document_dict(row) if row else None

    def create_document(
        self,
        *,
        doc_id: str,
        sha256: str,
        filename: str,
        kind: str,
        added_at: str,
        status: str = "queued",
    ) -> None:
        """Insert a document row after a duplicate check."""
        if status not in DOCUMENT_STATUSES:
            raise LibraryError("Estado de documento inválido.")
        with self._lock, self._write_guard(), self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO documents(id, sha256, filename, kind, status, added_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [doc_id, sha256, filename, kind, status, added_at],
                )
            except sqlite3.IntegrityError as exc:
                raise LibraryError("Este arquivo já está na biblioteca.") from exc

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """Return one document with its derived file URL."""
        self._require_available()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", [str(doc_id)]
            ).fetchone()
        return self._public_document(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        """List documents, most recently added first."""
        self._require_available()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY added_at DESC, id ASC"
            ).fetchall()
        return [self._public_document(row) for row in rows]

    def update_document(
        self,
        doc_id: str,
        *,
        title: str | None = None,
        author: str | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        """Update user-editable metadata fields."""
        fields: list[str] = []
        parameters: list[Any] = []
        if title is not None:
            clean_title = " ".join(str(title).split())[:500]
            fields.append("title = ?")
            parameters.append(clean_title or None)
        if author is not None:
            clean_author = " ".join(str(author).split())[:300]
            fields.append("author = ?")
            parameters.append(clean_author or None)
        if year is not None:
            value = int(year)
            if not 1000 <= value <= 2100:
                raise LibraryError("Informe um ano entre 1000 e 2100.")
            fields.append("year = ?")
            parameters.append(value)
        if not fields:
            document = self.get_document(doc_id)
            if document is None:
                raise LibraryNotFoundError("Documento não encontrado.")
            return document
        with self._lock, self._write_guard(), self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE documents SET {', '.join(fields)} WHERE id = ?",
                [*parameters, str(doc_id)],
            )
            if not cursor.rowcount:
                raise LibraryNotFoundError("Documento não encontrado.")
        document = self.get_document(doc_id)
        if document is None:  # pragma: no cover - deleted concurrently
            raise LibraryNotFoundError("Documento não encontrado.")
        return document

    def set_status(self, doc_id: str, status: str, *, error: str | None = None) -> None:
        """Update the processing status of a document."""
        if status not in DOCUMENT_STATUSES:
            raise LibraryError("Estado de documento inválido.")
        with self._lock, self._write_guard(), self._connect() as connection:
            connection.execute(
                "UPDATE documents SET status = ?, error = ? WHERE id = ?",
                [status, error, str(doc_id)],
            )

    def finish_document(
        self,
        doc_id: str,
        *,
        title: str | None,
        author: str | None,
        year: int | None,
        pages: int,
        chars: int,
        chunks: int,
        embedded: bool,
    ) -> None:
        """Store extraction metadata and mark a document as ready."""
        with self._lock, self._write_guard(), self._connect() as connection:
            connection.execute(
                "UPDATE documents SET title = ?, author = ?, year = ?, pages = ?, "
                "chars = ?, chunks = ?, status = 'ready', error = NULL, embedded = ? "
                "WHERE id = ?",
                [
                    title,
                    author,
                    year,
                    int(pages),
                    int(chars),
                    int(chunks),
                    1 if embedded else 0,
                    str(doc_id),
                ],
            )

    def replace_chunks(self, doc_id: str, chunks: Sequence[TextChunk]) -> list[int]:
        """Replace all chunks of ``doc_id`` and return their rowids in order."""
        with self._lock, self._write_guard(), self._connect() as connection:
            connection.execute("DELETE FROM chunks WHERE doc_id = ?", [str(doc_id)])
            rowids: list[int] = []
            for order, chunk in enumerate(chunks):
                cursor = connection.execute(
                    "INSERT INTO chunks(doc_id, ord, page_start, page_end, text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        str(doc_id),
                        order,
                        int(chunk.page_start),
                        int(chunk.page_end),
                        chunk.text,
                    ],
                )
                rowids.append(int(cursor.lastrowid or 0))
            return rowids

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document, its chunks, vectors and stored file."""
        self._require_available()
        with self._lock, self._write_guard():
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT sha256, kind FROM documents WHERE id = ?", [str(doc_id)]
                ).fetchone()
                if row is None:
                    return False
                chunk_rows = connection.execute(
                    "SELECT rowid FROM chunks WHERE doc_id = ?", [str(doc_id)]
                ).fetchall()
                connection.execute("DELETE FROM chunks WHERE doc_id = ?", [str(doc_id)])
                connection.execute("DELETE FROM documents WHERE id = ?", [str(doc_id)])
            rowids = [int(item[0]) for item in chunk_rows]
            if rowids:
                self._remove_vector_rows(rowids)
            file_path = self.files_dir / f"{row['sha256']}.{row['kind']}"
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def counts(self) -> dict[str, int]:
        """Return document/chunk counters and the pending queue size."""
        if not self.database_path.is_file():
            return {"documents": 0, "chunks": 0, "pending": 0}
        with self._lock, self._connect() as connection:
            documents = connection.execute("SELECT count(*) FROM documents").fetchone()
            chunks = connection.execute("SELECT count(*) FROM chunks").fetchone()
            pending = connection.execute(
                "SELECT count(*) FROM documents WHERE status IN ('queued', 'processing')"
            ).fetchone()
        return {
            "documents": int(documents[0]) if documents else 0,
            "chunks": int(chunks[0]) if chunks else 0,
            "pending": int(pending[0]) if pending else 0,
        }

    def chunk_count(self, doc_id: str) -> int:
        """Return how many chunks a document has."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM chunks WHERE doc_id = ?", [str(doc_id)]
            ).fetchone()
        return int(row[0]) if row else 0

    # -- vectors ----------------------------------------------------------------

    def _load_vectors(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if not self.vectors_path.is_file() or not self.vector_ids_path.is_file():
                return _empty_vectors()
            try:
                matrix = np.load(self.vectors_path, allow_pickle=False)
                rowids = np.load(self.vector_ids_path, allow_pickle=False)
            except (OSError, ValueError):
                return _empty_vectors()
            if (
                matrix.ndim != 2
                or matrix.shape[1] != VECTOR_DIMS
                or rowids.ndim != 1
                or matrix.shape[0] != rowids.shape[0]
            ):
                return _empty_vectors()
            return (
                np.ascontiguousarray(matrix, dtype=np.float32),
                np.asarray(rowids, dtype=np.int64),
            )

    def _save_vectors(self, matrix: np.ndarray, rowids: np.ndarray) -> None:
        clean = np.nan_to_num(
            np.asarray(matrix, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0
        )
        clean = np.ascontiguousarray(clean, dtype=np.float32)
        ids = np.asarray(rowids, dtype=np.int64)
        _atomic_save(self.vectors_path, clean)
        _atomic_save(self.vector_ids_path, ids)

    def add_vectors(self, rowids: Sequence[int], vectors: np.ndarray) -> None:
        """Insert or replace chunk vectors, keeping both numpy files aligned."""
        ids = np.asarray(list(rowids), dtype=np.int64)
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if ids.ndim != 1 or matrix.shape[0] != ids.shape[0]:
            raise LibraryError("Vetores incompatíveis com os trechos.")
        if matrix.shape[1] != VECTOR_DIMS:
            raise LibraryError("O modelo de embedding deve gerar vetores de 768 dimensões.")
        with self._lock, self._write_guard():
            current, current_ids = self._load_vectors()
            if current_ids.size:
                keep = ~np.isin(current_ids, ids)
                current = current[keep]
                current_ids = current_ids[keep]
            combined = np.vstack([current, matrix]) if current.size else matrix
            combined_ids = np.concatenate([current_ids, ids])
            self._save_vectors(combined, combined_ids)

    def _remove_vector_rows(self, rowids: Sequence[int]) -> None:
        with self._lock, self._write_guard():
            matrix, ids = self._load_vectors()
            if not ids.size:
                return
            keep = ~np.isin(ids, np.asarray(list(rowids), dtype=np.int64))
            self._save_vectors(matrix[keep], ids[keep])

    def missing_vector_chunks(self) -> list[tuple[int, str, str, str]]:
        """Return ``(rowid, doc_id, title, text)`` for chunks without vectors."""
        self._require_available()
        _matrix, ids = self._load_vectors()
        present = set(int(value) for value in ids.tolist())
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT c.rowid, c.doc_id, d.title, c.text FROM chunks c "
                "JOIN documents d ON d.id = c.doc_id ORDER BY c.rowid"
            ).fetchall()
        return [
            (int(row[0]), str(row[1]), str(row[2] or ""), str(row[3]))
            for row in rows
            if int(row[0]) not in present
        ]

    def mark_ready_embedded(self) -> None:
        """Mark documents whose chunks all have vectors as embedded."""
        _matrix, ids = self._load_vectors()
        present = set(int(value) for value in ids.tolist())
        with self._lock, self._write_guard(), self._connect() as connection:
            rows = connection.execute(
                "SELECT d.id, c.rowid FROM documents d "
                "JOIN chunks c ON c.doc_id = d.id"
            ).fetchall()
            missing_by_doc: dict[str, bool] = {}
            for doc_id, rowid in rows:
                missing_by_doc[str(doc_id)] = missing_by_doc.get(
                    str(doc_id), False
                ) or (int(rowid) not in present)
            ready = [doc_id for doc_id, missing in missing_by_doc.items() if not missing]
            if ready:
                placeholders = ",".join("?" for _ in ready)
                connection.execute(
                    f"UPDATE documents SET embedded = 1 WHERE id IN ({placeholders})",
                    ready,
                )

    # -- search -----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        embedder: Embedder | None = None,
        doc_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Search chunks textually and semantically, fusing ranks with RRF."""
        self._require_available()
        started = time.perf_counter()
        text = str(query or "").strip()[:2000]
        if not text:
            raise LibraryError("Digite um texto para buscar na biblioteca.")
        if page < 1:
            raise LibraryError("A página deve ser maior ou igual a 1.")
        if not 1 <= page_size <= 50:
            raise LibraryError("O tamanho da página deve estar entre 1 e 50.")
        document_filter = str(doc_id).strip() if doc_id else None
        if document_filter and self.get_document(document_filter) is None:
            raise LibraryNotFoundError("Documento não encontrado.")
        try:
            parsed = parse_fts_query(text)
        except SearchError as exc:
            raise LibraryError(str(exc)) from exc
        expression = " AND ".join(parsed.positives)
        relaxed = False
        warnings: list[str] = []

        with self._lock, self._connect() as connection:
            text_rows = self._text_search(
                connection, expression, parsed.negatives, document_filter
            )
            if not text_rows and len(parsed.positives) > 1:
                expression, text_rows, relaxed = self._relaxed_search(
                    connection,
                    parsed.positives,
                    parsed.negatives,
                    document_filter,
                )
        data: dict[int, dict[str, Any]] = {
            int(row["chunk_id"]): dict(row) for row in text_rows
        }
        scores: dict[int, float] = {}
        matched: dict[int, set[str]] = {}
        for rank, row in enumerate(text_rows, start=1):
            rowid = int(row["chunk_id"])
            scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank)
            matched.setdefault(rowid, set()).add("texto")

        semantic_used = self._can_embed(embedder)
        if semantic_used:
            try:
                assert embedder is not None
                semantic = self._semantic_search(embedder, text, document_filter)
            except Exception:  # noqa: BLE001 - graceful degradation is required
                semantic = []
                semantic_used = False
                warnings.append("semantic_unavailable")
            for rank, (rowid, _score) in enumerate(semantic, start=1):
                scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank)
                matched.setdefault(rowid, set()).add("significado")
                if rowid not in data:
                    row = self._chunk_row(rowid)
                    if row is not None:
                        data[rowid] = row
        else:
            warnings.append("semantic_unavailable")

        ordered = sorted(scores, key=lambda rowid: (-scores[rowid], rowid))
        grouped: list[int] = []
        per_document: Counter[str] = Counter()
        for rowid in ordered:
            row = data.get(rowid)
            if row is None:
                continue
            document_id = str(row["doc_id"])
            if per_document[document_id] >= MAX_RESULTS_PER_DOCUMENT:
                continue
            per_document[document_id] += 1
            grouped.append(rowid)

        total = len(grouped)
        offset = (page - 1) * page_size
        selected = grouped[offset : offset + page_size]
        results = [self._result(row=data[rowid], score=scores[rowid], matched=matched[rowid])
                   for rowid in selected if rowid in data]
        return {
            "query": text,
            "mode_used": "hybrid" if semantic_used else "text",
            "total": total,
            "total_is_estimate": semantic_used,
            "page": page,
            "page_size": page_size,
            "pages": math.ceil(total / page_size) if total else 0,
            "took_ms": int((time.perf_counter() - started) * 1000),
            "relaxed": relaxed,
            "warnings": warnings,
            "results": results,
        }

    def _relaxed_search(
        self,
        connection: sqlite3.Connection,
        positives: Sequence[str],
        negatives: Sequence[str],
        doc_id: str | None,
    ) -> tuple[str, list[sqlite3.Row], bool]:
        """Drop the most frequent terms before falling back to ``OR``."""
        frequencies = document_frequencies(connection, positives)
        dropped: set[int] = set()
        for index in relaxation_drops(frequencies):
            dropped.add(index)
            remaining = [
                item
                for position, item in enumerate(positives)
                if position not in dropped
            ]
            expression = " AND ".join(remaining)
            rows = self._text_search(connection, expression, negatives, doc_id)
            if rows:
                return expression, rows, True
        expression = " OR ".join(positives)
        rows = self._text_search(connection, expression, negatives, doc_id)
        return expression, rows, True

    def _text_search(
        self,
        connection: sqlite3.Connection,
        expression: str,
        negatives: Sequence[str],
        doc_id: str | None,
    ) -> list[sqlite3.Row]:
        clauses = ["chunks_fts MATCH ?"]
        parameters: list[Any] = [expression]
        if doc_id:
            clauses.append("d.id = ?")
            parameters.append(doc_id)
        for negative in negatives:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM chunks_fts AS neg "
                "WHERE neg.rowid = chunks_fts.rowid AND neg.rowid IN "
                "(SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?))"
            )
            parameters.append(negative)
        sql = (
            "SELECT c.rowid AS chunk_id, c.doc_id, c.ord, c.page_start, c.page_end, "
            "c.text, d.title, d.filename, "
            "snippet(chunks_fts, 0, '[[', ']]', ' … ', 30) AS snippet, "
            "bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
            "JOIN documents d ON d.id = c.doc_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY rank ASC LIMIT ?"
        )
        return connection.execute(
            sql, [*parameters, TEXT_CANDIDATES]
        ).fetchall()

    def _chunk_row(self, rowid: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT c.rowid AS chunk_id, c.doc_id, c.ord, c.page_start, c.page_end, "
                "c.text, d.title, d.filename, '' AS snippet "
                "FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.rowid = ?",
                [int(rowid)],
            ).fetchone()
        return dict(row) if row else None

    def _can_embed(self, embedder: Embedder | None) -> bool:
        if embedder is None or not self.vectors_path.is_file():
            return False
        try:
            return bool(embedder.ready())
        except Exception:  # noqa: BLE001 - optional engine
            return False

    def _semantic_search(
        self,
        embedder: Embedder,
        query: str,
        doc_id: str | None,
    ) -> list[tuple[int, float]]:
        matrix, ids = self._load_vectors()
        if not ids.size:
            return []
        vector = truncate_normalize(embedder.embed_queries([query]), VECTOR_DIMS)[0]
        vector = np.asarray(vector, dtype=np.float32)
        if vector.shape[0] != VECTOR_DIMS or not np.any(vector):
            return []
        mask = np.ones(ids.shape[0], dtype=bool)
        if doc_id:
            with self._lock, self._connect() as connection:
                rows = connection.execute(
                    "SELECT rowid FROM chunks WHERE doc_id = ?", [str(doc_id)]
                ).fetchall()
            allowed = np.asarray([int(row[0]) for row in rows], dtype=np.int64)
            mask = np.isin(ids, allowed)
        count = int(mask.sum())
        if count == 0:
            return []
        scores = matrix @ vector
        scores[~mask] = -np.inf
        top = min(SEMANTIC_CANDIDATES, count)
        if top >= scores.shape[0]:
            order = np.argsort(-scores)
        else:
            candidates = np.argpartition(scores, -top)[-top:]
            order = candidates[np.argsort(-scores[candidates])]
        return [
            (int(ids[index]), float(scores[index]))
            for index in order
            if np.isfinite(scores[index])
        ]

    def _result(
        self,
        *,
        row: dict[str, Any],
        score: float,
        matched: set[str],
    ) -> dict[str, Any]:
        snippet = str(row.get("snippet") or "").strip()
        if not snippet:
            snippet = str(row.get("text") or "")[:SNIPPET_CHARS]
        return {
            "chunk_id": int(row["chunk_id"]),
            "doc_id": str(row["doc_id"]),
            "title": row.get("title"),
            "filename": row.get("filename"),
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "snippet": snippet,
            "score": float(score),
            "matched_by": [item for item in ("texto", "significado") if item in matched],
        }

    def get_passage(self, chunk_id: int, context_chunks: int = 1) -> dict[str, Any]:
        """Return a chunk together with its neighbouring chunks."""
        self._require_available()
        if not 0 <= int(context_chunks) <= 3:
            raise LibraryError("O contexto deve estar entre 0 e 3 trechos.")
        with self._lock, self._connect() as connection:
            target = connection.execute(
                "SELECT c.rowid, c.doc_id, c.ord, c.page_start, c.page_end, c.text, "
                "d.title, d.filename FROM chunks c JOIN documents d ON d.id = c.doc_id "
                "WHERE c.rowid = ?",
                [int(chunk_id)],
            ).fetchone()
            if target is None:
                raise LibraryNotFoundError("Trecho não encontrado.")
            span = int(context_chunks)
            rows = connection.execute(
                "SELECT rowid, ord, page_start, page_end, text FROM chunks "
                "WHERE doc_id = ? AND ord BETWEEN ? AND ? ORDER BY ord",
                [
                    str(target["doc_id"]),
                    int(target["ord"]) - span,
                    int(target["ord"]) + span,
                ],
            ).fetchall()
        chunks = [
            {
                "chunk_id": int(row["rowid"]),
                "ord": int(row["ord"]),
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "text": str(row["text"]),
                "is_target": int(row["rowid"]) == int(chunk_id),
            }
            for row in rows
        ]
        return {
            "chunk_id": int(chunk_id),
            "doc_id": str(target["doc_id"]),
            "title": target["title"],
            "filename": target["filename"],
            "page_start": target["page_start"],
            "page_end": target["page_end"],
            "text": "\n\n".join(item["text"] for item in chunks),
            "chunks": chunks,
        }

    # -- files ------------------------------------------------------------------

    def file_path_for(self, sha256: str, kind: str) -> Path:
        """Return the stored file path for a content hash and kind."""
        return self.files_dir / f"{sha256}.{kind}"

    def document_file(self, doc_id: str) -> Path:
        """Return the stored file path of a document."""
        document = self.get_document(doc_id)
        if document is None:
            raise LibraryNotFoundError("Documento não encontrado.")
        path = self.file_path_for(str(document["sha256"]), str(document["kind"]))
        if not path.is_file():
            raise LibraryNotFoundError("O arquivo deste documento não foi encontrado.")
        return path

    def _public_document(self, row: sqlite3.Row) -> dict[str, Any]:
        data = _document_dict(row)
        data["embedded"] = bool(data.get("embedded"))
        data["file_url"] = f"/api/library/documents/{data['id']}/file"
        path = self.file_path_for(str(data["sha256"]), str(data["kind"]))
        try:
            data["file_size"] = path.stat().st_size if path.is_file() else 0
        except OSError:
            data["file_size"] = 0
        return data


def _document_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _empty_vectors() -> tuple[np.ndarray, np.ndarray]:
    return (
        np.empty((0, VECTOR_DIMS), dtype=np.float32),
        np.empty(0, dtype=np.int64),
    )


def _atomic_save(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.save(handle, array, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "ATTACHMENT_KINDS",
    "DOCUMENT_STATUSES",
    "LibraryError",
    "LibraryNotFoundError",
    "LibraryStore",
    "MEDIA_TYPES",
    "RRF_K",
    "VECTOR_DIMS",
    "media_type_for",
    "safe_filename",
    "sha256_file",
]
