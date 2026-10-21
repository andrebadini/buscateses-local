"""Read-only catalog search: FTS5, vectors, filters, facets and RRF."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np

from buscateses.catalog.builder import (
    CATALOG_DB,
    FILTERS_FILE,
    VECTOR_DIMS,
    VECTORS_FILE,
    unpack_text,
)
from buscateses.embeddings.base import Embedder, truncate_normalize
from buscateses.fts_relax import (
    create_vocabulary,
    document_frequencies,
    relaxation_drops,
)
from buscateses.textcase import display_record, name_case, truecase

RRF_K = 60
TEXT_CANDIDATES = 400
SEMANTIC_CANDIDATES = 400
VECTOR_BLOCK = 262_144
SNIPPET_CHARS = 320
#: Exact FTS counts stop here; larger totals are reported as a lower bound.
TEXT_COUNT_LIMIT = 10_000
#: Maximum time one textual SQL statement may run before it is aborted.
TEXT_TIMEOUT_SECONDS = 2.5
#: SQLite VM instructions between progress-handler callbacks.
PROGRESS_STEPS = 10_000
_TEXT_TIMEOUT_MESSAGE = (
    "A busca textual demorou demais para responder. "
    "Tente termos mais específicos ou aplique filtros."
)
MODES = {"hybrid", "text", "semantic"}
SCOPES = {"all", "title", "abstract", "author", "advisor", "keywords"}
SORTS = {"relevance", "year_desc", "year_asc"}
_SCOPE_COLUMNS = {
    "title": "title",
    "abstract": "abstract",
    "author": "author",
    "advisor": "advisors",
    "keywords": "keywords",
}
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_OPERATORS = {"and", "or", "not", "near"}
#: Short explicit list of Portuguese function words ignored outside quotes.
_STOPWORDS = frozenset(
    {
        "a",
        "ao",
        "aos",
        "as",
        "ate",
        "com",
        "como",
        "contra",
        "da",
        "das",
        "de",
        "desde",
        "do",
        "dos",
        "e",
        "ela",
        "elas",
        "ele",
        "eles",
        "em",
        "entre",
        "era",
        "estar",
        "essa",
        "esse",
        "esta",
        "este",
        "eu",
        "foi",
        "ha",
        "isso",
        "isto",
        "ja",
        "mais",
        "mas",
        "muito",
        "na",
        "nao",
        "nas",
        "nem",
        "no",
        "nos",
        "num",
        "numa",
        "o",
        "onde",
        "os",
        "ou",
        "para",
        "pela",
        "pelas",
        "pelo",
        "pelos",
        "por",
        "porque",
        "pra",
        "qual",
        "quais",
        "quando",
        "que",
        "quem",
        "se",
        "sem",
        "seu",
        "seus",
        "sua",
        "suas",
        "sao",
        "ser",
        "sob",
        "sobre",
        "tem",
        "ter",
        "tambem",
        "um",
        "uma",
        "umas",
        "uns",
        "voce",
        "voces",
    }
)


class SearchError(ValueError):
    """Raised for invalid queries or unavailable catalog data."""


class SearchNotFoundError(SearchError):
    """Raised when a requested thesis id does not exist in the catalog."""


class TextQueryTimeout(SearchError):
    """Raised when a textual query exceeds its time budget."""


@dataclass(frozen=True)
class SearchQuery:
    """Public search parameters (SPEC §4)."""

    q: str = ""
    mode: str = "hybrid"
    scope: str = "all"
    year_min: int | None = None
    year_max: int | None = None
    degree: tuple[str, ...] = ()
    uf: tuple[str, ...] = ()
    institution: tuple[str, ...] = ()
    great_area: tuple[str, ...] = ()
    knowledge_area: tuple[str, ...] = ()
    sort: str = "relevance"
    page: int = 1
    page_size: int = 20


@dataclass
class SearchResult:
    """Search response shared by the API, CLI and future MCP server."""

    query: str
    mode_used: str
    total: int
    total_is_estimate: bool
    page: int
    page_size: int
    pages: int
    took_ms: int
    relaxed: bool
    total_capped: bool = False
    warnings: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    facets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible API response."""
        return {
            "query": self.query,
            "mode_used": self.mode_used,
            "total": self.total,
            "total_is_estimate": self.total_is_estimate,
            "total_capped": self.total_capped,
            "page": self.page,
            "page_size": self.page_size,
            "pages": self.pages,
            "took_ms": self.took_ms,
            "relaxed": self.relaxed,
            "warnings": list(self.warnings),
            "results": self.results,
            "facets": self.facets,
        }


@dataclass(frozen=True)
class _ParsedQuery:
    positives: tuple[str, ...]
    negatives: tuple[str, ...]


class _QueryBudget:
    """SQLite progress handler that aborts a statement after a deadline."""

    def __init__(self, deadline: float, clock: Callable[[], float]) -> None:
        self._deadline = deadline
        self._clock = clock

    def expired(self) -> bool:
        """Return whether the deadline has passed."""
        return self._clock() >= self._deadline

    def progress(self) -> int:
        """Return nonzero once the deadline has passed (SQLite aborts)."""
        return 1 if self.expired() else 0


class CatalogSearch:
    """Thread-safe read-only search over one installed catalog release."""

    def __init__(
        self,
        release_dir: str | Path,
        embedder: Embedder | None = None,
        *,
        text_timeout: float = TEXT_TIMEOUT_SECONDS,
        progress_steps: int = PROGRESS_STEPS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.release_dir = Path(release_dir)
        self._text_timeout = max(0.0, float(text_timeout))
        self._progress_steps = max(1, int(progress_steps))
        self._clock = clock
        self.database_path = self.release_dir / CATALOG_DB
        if not self.database_path.is_file():
            raise SearchError("O catálogo local não está instalado.")
        self._embedder = embedder
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._vectors: np.ndarray | None = None
        self._vector_dtype = np.dtype(np.float32)
        self._filter_arrays: dict[str, np.ndarray] | None = None
        self._format_version = _read_format_version(self.database_path)
        self._columns = _select_columns(self._format_version)
        vectors_path = self.release_dir / VECTORS_FILE
        if vectors_path.is_file():
            try:
                loaded = np.load(vectors_path, mmap_mode="r")
                if loaded.ndim == 2 and loaded.shape[1] == VECTOR_DIMS:
                    self._vectors = loaded
                    self._vector_dtype = loaded.dtype
            except (OSError, ValueError):
                self._vectors = None
        filters_path = self.release_dir / FILTERS_FILE
        if filters_path.is_file():
            try:
                with np.load(filters_path, allow_pickle=False) as payload:
                    self._filter_arrays = {key: payload[key].copy() for key in payload.files}
            except (OSError, ValueError):
                self._filter_arrays = None

    @property
    def embedder(self) -> Embedder | None:
        """Return the configured embedder, if any."""
        return self._embedder

    def set_embedder(self, embedder: Embedder | None) -> None:
        """Replace the embedder (used after configuration changes)."""
        self._embedder = embedder

    def search(self, query: SearchQuery) -> SearchResult:
        """Execute a textual, semantic or hybrid search."""
        started = time.perf_counter()
        normalized = self._normalize_query(query)
        parsed = parse_fts_query(normalized.q, normalized.scope)
        connection = self._connection()
        warnings: list[str] = []
        mode_used = normalized.mode
        semantic_ready = normalized.mode in {"hybrid", "semantic"} and self._can_embed()
        if normalized.mode in {"hybrid", "semantic"} and not semantic_ready:
            warnings.append("semantic_unavailable")
            mode_used = "text"

        if mode_used == "text":
            return self._run_text_search(
                connection, normalized, parsed, warnings, started
            )
        return self._vector_search(
            connection, normalized, parsed, mode_used, warnings, started
        )

    def warm_up(self) -> None:
        """Fault in vectors and FTS pages so the first real query is fast."""
        vectors = self._vectors
        if vectors is not None:
            for start in range(0, vectors.shape[0], VECTOR_BLOCK):
                end = min(start + VECTOR_BLOCK, vectors.shape[0])
                # A strided column touches every page without copying the matrix.
                float(np.asarray(vectors[start:end, 0], dtype=np.float32).sum())
        connection = self._connection()
        try:
            connection.execute(
                "SELECT rowid FROM theses_fts WHERE theses_fts MATCH ? LIMIT 1",
                ['"de"'],
            ).fetchone()
            connection.execute("SELECT rowid FROM theses LIMIT 1").fetchone()
            connection.execute("SELECT kind FROM facets LIMIT 1").fetchone()
        except sqlite3.Error:
            pass

    def get(self, thesis_id: str) -> dict[str, Any]:
        """Return the full record for ``thesis_id``."""
        connection = self._connection()
        row = connection.execute(
            f"SELECT rowid, {self._columns} FROM theses WHERE id = ?",
            [str(thesis_id)],
        ).fetchone()
        if row is None:
            raise SearchNotFoundError("Tese não encontrada.")
        result = self._decode_row(row)
        result.pop("rowid", None)
        return display_record(result)

    def similar(self, thesis_id: str, k: int = 10) -> list[dict[str, Any]]:
        """Return semantically similar theses, excluding the record itself."""
        if not 1 <= int(k) <= 100:
            raise SearchError("A quantidade de similares deve estar entre 1 e 100.")
        connection = self._connection()
        row = connection.execute(
            "SELECT rowid FROM theses WHERE id = ?", [str(thesis_id)]
        ).fetchone()
        if row is None:
            raise SearchNotFoundError("Tese não encontrada.")
        if self._vectors is None:
            raise SearchError("Busca semântica indisponível.")
        rowid = int(row[0])
        vector = np.asarray(self._vectors[rowid - 1], dtype=np.float32)
        if not np.any(vector):
            return []
        mask = np.ones(self._vectors.shape[0], dtype=bool)
        arrays = self._filter_arrays
        if arrays is not None:
            available = arrays.get("has_vector")
            if available is not None and available.shape[0] == mask.shape[0]:
                mask &= available.astype(bool)
        mask[rowid - 1] = False
        candidates = self._semantic_candidates(vector, mask, int(k))
        if not candidates:
            return []
        rows = self._rows_by_rowid([item[0] for item in candidates])
        results: list[dict[str, Any]] = []
        for candidate_rowid, score in candidates:
            source = rows.get(candidate_rowid)
            if source is None:
                continue
            item = display_record(_public_row(source))
            item["score"] = float(score)
            item["matched_by"] = ["significado"]
            item["snippet"] = _snippet_for(source, ())
            results.append(item)
        return results

    def stats(self) -> dict[str, Any]:
        """Return catalog counters and release metadata."""
        connection = self._connection()
        values = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key, value FROM meta").fetchall()
        }
        years: list[int] = []
        if "," in values.get("years", ""):
            try:
                years = [int(part) for part in values["years"].split(",", 1)]
            except ValueError:
                years = []
        return {
            "records": _as_int(values.get("records"), 0),
            "version": values.get("version"),
            "years": years,
            "built_at": values.get("built_at"),
            "missing_vectors": _as_int(values.get("missing_vectors"), 0),
        }

    def global_facets(self) -> dict[str, list[dict[str, Any]]]:
        """Return global facets stored in the release."""
        connection = self._connection()
        rows = connection.execute(
            "SELECT kind, value, label, count FROM facets "
            "ORDER BY kind, count DESC, value ASC"
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {
            kind: []
            for kind in (
                "degree",
                "uf",
                "great_area",
                "knowledge_area",
                "institution_acronym",
                "year",
            )
        }
        for kind, value, label, count in rows:
            text = str(label)
            if kind in {
                "degree",
                "great_area",
                "knowledge_area",
                "institution_acronym",
            }:
                # Older catalogs stored raw uppercase labels; normalize them.
                text = name_case(text) or text
            result.setdefault(str(kind), []).append(
                {"value": str(value), "label": text, "count": int(count)}
            )
        return result

    def facets(self) -> dict[str, list[dict[str, Any]]]:
        """Alias for :meth:`global_facets`."""
        return self.global_facets()

    def close(self) -> None:
        """Close every SQLite connection and release the memory-mapped vectors.

        The catalog may be replaced while the app is running on Windows, where
        an open file cannot be deleted. Connections are therefore tracked for
        all threads, not only the caller's.
        """
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._local.connection = None
        vectors = self._vectors
        self._vectors = None
        mmap = getattr(vectors, "_mmap", None)
        if mmap is not None:
            try:
                mmap.close()
            except (BufferError, OSError, ValueError):
                pass

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            uri = f"file:{quote(str(self.database_path))}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=15.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA mmap_size=268435456")
            connection.execute("PRAGMA cache_size=-65536")
            # query_only would block the temporary fts5vocab table, so create
            # it first and then make the connection read-only.
            create_vocabulary(connection, "theses_fts")
            connection.execute("PRAGMA query_only=ON")
            self._local.connection = connection
            with self._connections_lock:
                self._connections.add(connection)
        return connection

    def _normalize_query(self, query: SearchQuery) -> SearchQuery:
        q = str(query.q or "").strip()[:2000]
        if not q:
            raise SearchError("Digite uma consulta para buscar.")
        mode = str(query.mode or "hybrid").strip().lower()
        if mode not in MODES:
            raise SearchError("Modo de busca inválido.")
        scope = str(query.scope or "all").strip().lower()
        if scope not in SCOPES:
            raise SearchError("Escopo de busca inválido.")
        sort = str(query.sort or "relevance").strip().lower()
        if sort not in SORTS:
            raise SearchError("Ordenação inválida.")
        if int(query.page) < 1:
            raise SearchError("A página deve ser maior ou igual a 1.")
        if not 1 <= int(query.page_size) <= 100:
            raise SearchError("O tamanho da página deve estar entre 1 e 100.")
        year_min = _optional_int(query.year_min)
        year_max = _optional_int(query.year_max)
        if year_min is not None and year_max is not None and year_min > year_max:
            raise SearchError("O ano inicial não pode ser maior que o ano final.")
        return SearchQuery(
            q=q,
            mode=mode,
            scope=scope,
            year_min=year_min,
            year_max=year_max,
            degree=_clean_values(query.degree),
            uf=_clean_values(query.uf),
            institution=_clean_values(query.institution),
            great_area=_clean_values(query.great_area),
            knowledge_area=_clean_values(query.knowledge_area),
            sort=sort,
            page=int(query.page),
            page_size=int(query.page_size),
        )

    def _run_text_search(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        warnings: list[str],
        started: float,
    ) -> SearchResult:
        """Run the exact-text pipeline, raising :class:`TextQueryTimeout`."""
        offset = (query.page - 1) * query.page_size
        limit = max(TEXT_CANDIDATES, offset + query.page_size)
        expression, candidates, relaxed = self._relaxed_candidates(
            connection, query, parsed, limit
        )
        total, capped = self._text_count(
            connection, expression, parsed.negatives, query
        )
        return self._text_search(
            connection,
            query,
            parsed,
            expression,
            relaxed,
            total,
            capped,
            candidates,
            warnings,
            started,
        )

    def _relaxed_candidates(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        limit: int,
    ) -> tuple[str, list[tuple[int, float]], bool]:
        """Return the best expression and its ranked candidates.

        The full ``AND`` is tried first.  If it is empty, the most frequent
        expression is dropped one at a time (keeping at least two terms) and
        the shortest selective conjunction wins.  A plain ``OR`` is the last
        resort because it can match hundreds of thousands of documents.
        """
        expression = _positive_expression(parsed.positives, "AND", query.scope)
        candidates = self._text_candidates(connection, query, parsed, expression, limit)
        if candidates or len(parsed.positives) <= 1:
            return expression, candidates, False
        frequencies = document_frequencies(connection, parsed.positives)
        dropped: set[int] = set()
        for index in relaxation_drops(frequencies):
            dropped.add(index)
            remaining = [
                item for position, item in enumerate(parsed.positives)
                if position not in dropped
            ]
            candidate_expression = _positive_expression(
                remaining, "AND", query.scope
            )
            candidates = self._text_candidates(
                connection, query, parsed, candidate_expression, limit
            )
            if candidates:
                return candidate_expression, candidates, True
        fallback = _positive_expression(parsed.positives, "OR", query.scope)
        candidates = self._text_candidates(connection, query, parsed, fallback, limit)
        return fallback, candidates, True

    def _text_search(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        expression: str,
        relaxed: bool,
        total: int,
        total_capped: bool,
        candidates: Sequence[tuple[int, float]],
        warnings: list[str],
        started: float,
    ) -> SearchResult:
        offset = (query.page - 1) * query.page_size
        if query.sort in {"year_desc", "year_asc"}:
            page_rows = self._text_page(connection, query, parsed, expression, offset)
        else:
            page_rows = list(candidates[offset : offset + query.page_size])
        candidate_rowids = [rowid for rowid, _score in candidates]
        rowids = [item[0] for item in page_rows]
        sources = self._rows_by_rowid(rowids)
        terms = _terms_from_positives(parsed.positives)
        results: list[dict[str, Any]] = []
        for position, (rowid, _score) in enumerate(page_rows):
            source = sources.get(rowid)
            if source is None:
                continue
            item = display_record(_public_row(source))
            item["score"] = 1.0 / (RRF_K + offset + position + 1)
            item["matched_by"] = ["texto"]
            item["snippet"] = _snippet_for(source, terms)
            results.append(item)
        facets = self._facets_for_rowids(candidate_rowids)
        pages = math.ceil(total / query.page_size) if total else 0
        return SearchResult(
            query=query.q,
            mode_used="text",
            total=total,
            total_is_estimate=total_capped,
            total_capped=total_capped,
            page=query.page,
            page_size=query.page_size,
            pages=pages,
            took_ms=int((time.perf_counter() - started) * 1000),
            relaxed=relaxed,
            warnings=warnings,
            results=results,
            facets=facets,
        )

    def _vector_search(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        mode_used: str,
        warnings: list[str],
        started: float,
    ) -> SearchResult:
        warnings = list(warnings)
        text_ranked: list[tuple[int, float]] = []
        relaxed = False
        if mode_used != "semantic":
            try:
                _expression, text_ranked, relaxed = self._relaxed_candidates(
                    connection, query, parsed, TEXT_CANDIDATES
                )
            except TextQueryTimeout:
                # The semantic channel is still useful; skip the slow text side.
                warnings.append("text_slow")
        semantic_ranked: list[tuple[int, float]] = []
        semantic_error = False
        try:
            semantic_ranked = self._semantic_ranked(query)
        except Exception:  # noqa: BLE001 - graceful degradation is required
            semantic_error = True
        if semantic_error:
            if "semantic_unavailable" not in warnings:
                warnings.append("semantic_unavailable")
            return self._run_text_search(connection, query, parsed, warnings, started)

        if mode_used == "semantic":
            ordered = [(rowid, score, ("significado",)) for rowid, score in semantic_ranked]
        else:
            scores: dict[int, float] = {}
            matched: dict[int, set[str]] = {}
            for rank, (rowid, _score) in enumerate(text_ranked, start=1):
                scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank)
                matched.setdefault(rowid, set()).add("texto")
            for rank, (rowid, _score) in enumerate(semantic_ranked, start=1):
                scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank)
                matched.setdefault(rowid, set()).add("significado")
            ordered = [
                (rowid, scores[rowid], tuple(_ordered_matched(matched.get(rowid, set()))))
                for rowid in sorted(scores, key=lambda item: (-scores[item], item))
            ]

        if query.sort in {"year_desc", "year_asc"}:
            years = self._years_for_rowids([item[0] for item in ordered])
            reverse = query.sort == "year_desc"
            ordered.sort(
                key=lambda item: (
                    years.get(item[0], 0),
                    item[1] if reverse else -item[1],
                ),
                reverse=reverse,
            )
        total = len(ordered)
        offset = (query.page - 1) * query.page_size
        page_items = ordered[offset : offset + query.page_size]
        sources = self._rows_by_rowid([item[0] for item in page_items])
        terms = _terms_from_positives(parsed.positives)
        results: list[dict[str, Any]] = []
        for rowid, score, matched_by in page_items:
            source = sources.get(rowid)
            if source is None:
                continue
            item = display_record(_public_row(source))
            item["score"] = float(score)
            item["matched_by"] = list(matched_by)
            item["snippet"] = _snippet_for(source, terms)
            results.append(item)
        facets = self._facets_for_rowids([item[0] for item in ordered])
        pages = math.ceil(total / query.page_size) if total else 0
        return SearchResult(
            query=query.q,
            mode_used=mode_used,
            total=total,
            total_is_estimate=mode_used in {"hybrid", "semantic"},
            total_capped=False,
            page=query.page,
            page_size=query.page_size,
            pages=pages,
            took_ms=int((time.perf_counter() - started) * 1000),
            relaxed=relaxed,
            warnings=warnings,
            results=results,
            facets=facets,
        )

    def _text_count(
        self,
        connection: sqlite3.Connection,
        expression: str,
        negatives: Sequence[str],
        query: SearchQuery,
    ) -> tuple[int, bool]:
        """Return ``(total, capped)``; exact counts stop at 10,001 rows."""
        join, where, parameters = self._text_source(
            expression, negatives, query
        )
        row = self._fetch_one(
            connection,
            f"SELECT count(*) FROM (SELECT 1 FROM theses_fts{join} "
            f"WHERE {where} LIMIT {TEXT_COUNT_LIMIT + 1})",
            parameters,
        )
        count = int(row[0]) if row else 0
        return min(count, TEXT_COUNT_LIMIT), count > TEXT_COUNT_LIMIT

    def _text_candidates(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        expression: str,
        limit: int,
    ) -> list[tuple[int, float]]:
        join, where, parameters = self._text_source(
            expression, parsed.negatives, query
        )
        if join:
            rowid = "t.rowid"
            order = "rank ASC, t.year DESC, t.rowid ASC"
        else:
            rowid = "rowid"
            order = "rank ASC, rowid ASC"
        rows = self._fetch_all(
            connection,
            f"SELECT {rowid} AS rowid, {_BM25} AS rank FROM theses_fts{join} "
            f"WHERE {where} ORDER BY {order} LIMIT ?",
            [*parameters, int(limit)],
        )
        return [(int(row[0]), float(row[1])) for row in rows]

    def _text_page(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
        parsed: _ParsedQuery,
        expression: str,
        offset: int,
    ) -> list[tuple[int, float]]:
        join, where, parameters = self._text_source(
            expression, parsed.negatives, query, need_theses=True
        )
        if query.sort == "year_asc":
            order = "t.year ASC, rank ASC, t.rowid ASC"
        elif query.sort == "year_desc":
            order = "t.year DESC, rank ASC, t.rowid ASC"
        else:
            order = "rank ASC, t.year DESC, t.rowid ASC"
        rows = self._fetch_all(
            connection,
            f"SELECT t.rowid, {_BM25} AS rank FROM theses_fts{join} "
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*parameters, query.page_size, int(offset)],
        )
        return [(int(row[0]), float(row[1])) for row in rows]

    def _text_source(
        self,
        expression: str,
        negatives: Sequence[str],
        query: SearchQuery,
        *,
        need_theses: bool = False,
    ) -> tuple[str, str, list[Any]]:
        """Build the JOIN/WHERE clause shared by textual queries."""
        clauses = ["theses_fts MATCH ?"]
        parameters: list[Any] = [expression]
        filters, filter_parameters = _filter_clauses(query)
        if filters:
            clauses.append(filters)
            parameters.extend(filter_parameters)
        for negative in negatives:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM theses_fts AS neg "
                "WHERE neg.rowid = theses_fts.rowid AND neg.rowid IN "
                "(SELECT rowid FROM theses_fts WHERE theses_fts MATCH ?))"
            )
            parameters.append(negative)
        join = (
            " JOIN theses t ON t.rowid = theses_fts.rowid"
            if filters or need_theses
            else ""
        )
        return join, " AND ".join(clauses), parameters

    @contextmanager
    def _text_budget(self, connection: sqlite3.Connection) -> Iterator[None]:
        """Abort the statement through the SQLite progress handler."""
        budget = _QueryBudget(self._clock() + self._text_timeout, self._clock)
        connection.set_progress_handler(budget.progress, self._progress_steps)
        try:
            yield
        except sqlite3.OperationalError as exc:
            # An interrupt during virtual-table setup surfaces as
            # "vtable constructor failed", so also trust the clock.
            if "interrupt" in str(exc).lower() or budget.expired():
                raise TextQueryTimeout(_TEXT_TIMEOUT_MESSAGE) from exc
            raise
        finally:
            connection.set_progress_handler(None, 0)

    def _fetch_all(
        self,
        connection: sqlite3.Connection,
        sql: str,
        parameters: Sequence[Any],
    ) -> list[sqlite3.Row]:
        with self._text_budget(connection):
            return connection.execute(sql, list(parameters)).fetchall()

    def _fetch_one(
        self,
        connection: sqlite3.Connection,
        sql: str,
        parameters: Sequence[Any],
    ) -> sqlite3.Row | None:
        with self._text_budget(connection):
            return connection.execute(sql, list(parameters)).fetchone()

    def _decode_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a dict, expanding compressed abstracts."""
        data = _row_to_dict(row)
        if self._format_version >= 2:
            for key in ("abstract", "abstract_en"):
                value = data.get(key)
                if isinstance(value, (bytes, bytearray, memoryview)):
                    data[key] = unpack_text(value)
        return data

    def _rows_by_rowid(self, rowids: Sequence[int]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        connection = self._connection()
        for chunk in _chunks(list(dict.fromkeys(rowids)), 500):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT rowid, {self._columns} FROM theses WHERE rowid IN ({placeholders})",
                list(chunk),
            ).fetchall()
            for row in rows:
                result[int(row["rowid"])] = self._decode_row(row)
        return result

    def _years_for_rowids(self, rowids: Sequence[int]) -> dict[int, int]:
        result: dict[int, int] = {}
        connection = self._connection()
        for chunk in _chunks(list(dict.fromkeys(rowids)), 500):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT rowid, year FROM theses WHERE rowid IN ({placeholders})",
                list(chunk),
            ).fetchall()
            for rowid, year in rows:
                result[int(rowid)] = int(year or 0)
        return result

    def _facets_for_rowids(self, rowids: Sequence[int]) -> dict[str, list[dict[str, Any]]]:
        counter: dict[str, Counter[str]] = {
            "degree": Counter(),
            "uf": Counter(),
            "great_area": Counter(),
            "decade": Counter(),
        }
        connection = self._connection()
        for chunk in _chunks(list(dict.fromkeys(rowids)), 500):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                "SELECT degree, uf, great_area, year FROM theses "
                f"WHERE rowid IN ({placeholders})",
                list(chunk),
            ).fetchall()
            for row in rows:
                for kind in ("degree", "uf", "great_area"):
                    value = row[kind]
                    if value:
                        counter[kind][str(value)] += 1
                if row["year"]:
                    decade = (int(row["year"]) // 10) * 10
                    counter["decade"][str(decade)] += 1
        return {
            kind: [
                {
                    "value": value,
                    "label": (
                        f"{value}–{int(value) + 9}"
                        if kind == "decade"
                        else name_case(value)
                    ),
                    "count": count,
                }
                for value, count in sorted(
                    values.items(),
                    key=(
                        (lambda item: (int(item[0]), item[0]))
                        if kind == "decade"
                        else (lambda item: (-item[1], item[0]))
                    ),
                )
            ]
            for kind, values in counter.items()
        }

    def _can_embed(self) -> bool:
        if self._embedder is None or self._vectors is None:
            return False
        try:
            return bool(self._embedder.ready())
        except Exception:  # noqa: BLE001 - optional engine
            return False

    def _semantic_ranked(self, query: SearchQuery) -> list[tuple[int, float]]:
        assert self._embedder is not None
        matrix = np.asarray(self._embedder.embed_queries([query.q]), dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape[0] != 1:
            raise SearchError("O modelo de embedding retornou um vetor inválido.")
        vector = np.asarray(truncate_normalize(matrix, VECTOR_DIMS)[0], dtype=np.float32)
        if not np.any(vector) or self._vectors is None:
            return []
        if vector.shape[0] != self._vectors.shape[1]:
            raise SearchError("O modelo de embedding é incompatível com o catálogo.")
        mask = self._filter_mask(query)
        return self._semantic_candidates(vector, mask, SEMANTIC_CANDIDATES)

    def _filter_mask(self, query: SearchQuery) -> np.ndarray:
        assert self._vectors is not None
        count = self._vectors.shape[0]
        arrays = self._filter_arrays
        if arrays is None or len(arrays.get("year", ())) != count:
            return self._sql_mask(query, count)
        mask = np.ones(count, dtype=bool)
        if "has_vector" in arrays and arrays["has_vector"].shape[0] == count:
            mask &= arrays["has_vector"].astype(bool)
        years = arrays.get("year")
        if years is not None:
            if query.year_min is not None:
                mask &= years >= query.year_min
            if query.year_max is not None:
                mask &= years <= query.year_max
        for kind, values in (
            ("degree", query.degree),
            ("uf", query.uf),
            ("institution_acronym", query.institution),
            ("great_area", query.great_area),
            ("knowledge_area", query.knowledge_area),
        ):
            if not values:
                continue
            codes = arrays.get(kind)
            vocab = arrays.get(f"{kind}_vocab")
            if codes is None or vocab is None or codes.shape[0] != count:
                return self._sql_mask(query, count)
            mapping = {str(value): index for index, value in enumerate(vocab.tolist())}
            selected = [mapping[value] for value in values if value in mapping]
            if not selected:
                return np.zeros(count, dtype=bool)
            mask &= np.isin(codes, np.asarray(selected, dtype=np.int32))
        return mask

    def _sql_mask(self, query: SearchQuery, count: int) -> np.ndarray:
        mask = np.zeros(count, dtype=bool)
        filters, parameters = _filter_clauses(query)
        where = filters or "1=1"
        rows = self._connection().execute(
            f"SELECT rowid FROM theses AS t WHERE {where}", parameters
        ).fetchall()
        for (rowid,) in rows:
            index = int(rowid) - 1
            if 0 <= index < count:
                mask[index] = True
        return mask

    def _semantic_candidates(
        self,
        vector: np.ndarray,
        mask: np.ndarray,
        limit: int,
    ) -> list[tuple[int, float]]:
        vectors = self._vectors
        if vectors is None:
            return []
        count = vectors.shape[0]
        if count == 0 or limit <= 0:
            return []
        valid = int(mask.sum())
        if valid == 0:
            return []
        scores = np.full(count, -np.inf, dtype=np.float32)
        unit = np.ascontiguousarray(vector, dtype=np.float32)
        for start in range(0, count, VECTOR_BLOCK):
            end = min(start + VECTOR_BLOCK, count)
            block = vectors[start:end]
            if block.dtype != np.float32:
                block = block.astype(np.float32)
            np.dot(block, unit, out=scores[start:end])
        scores[~mask] = -np.inf
        top_k = min(limit, valid)
        if top_k >= count:
            order = np.argsort(-scores)
        else:
            top = np.argpartition(scores, -top_k)[-top_k:]
            order = top[np.argsort(-scores[top])]
        return [
            (int(index) + 1, float(scores[index]))
            for index in order
            if np.isfinite(scores[index])
        ]


_COLUMN_NAMES = (
    "id, year, defense_date, title, author, advisors, degree, institution, "
    "institution_acronym, uf, region, program, program_code, great_area, "
    "knowledge_area, evaluation_area, concentration_area, research_line, "
    "keywords, keywords_en, {abstract}, {abstract_en}, language, pages, library, url, source"
)
_BM25 = "bm25(theses_fts, 8.0, 4.0, 1.0, 3.0, 2.0, 1.0, 1.0)"


def _select_columns(format_version: int) -> str:
    """Return the SELECT list matching the release's on-disk format."""
    if format_version >= 2:
        return _COLUMN_NAMES.format(
            abstract="abstract_z AS abstract",
            abstract_en="abstract_en_z AS abstract_en",
        )
    return _COLUMN_NAMES.format(abstract="abstract", abstract_en="abstract_en")


def _read_format_version(database_path: Path) -> int:
    """Read ``meta.format_version`` (defaulting to the legacy format 1)."""
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(database_path))}?mode=ro", uri=True, timeout=5.0
        )
        try:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'format_version'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return 1
    if row is None:
        return 1
    try:
        return max(1, int(row[0]))
    except (TypeError, ValueError):
        return 1


def _fold_token(token: str) -> str:
    """Lowercase *token* without diacritics for stopword matching."""
    decomposed = unicodedata.normalize("NFD", token.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _drop_stopwords(phrase: str) -> str:
    """Remove unquoted stopwords from a phrase, keeping content words."""
    kept = [
        token
        for token in _TOKEN_RE.findall(phrase)
        if _fold_token(token) not in _STOPWORDS
    ]
    return " ".join(kept)


def parse_fts_query(query: str, scope: str = "all") -> _ParsedQuery:
    """Safely turn user input into FTS5 phrases.

    Quotes create phrases; a leading dash excludes a term. FTS operators typed
    by users are treated as separators, never as raw syntax. Stopwords are
    dropped from unquoted terms as long as at least one content word remains.
    """
    entries: list[tuple[str, bool, bool]] = []
    pending_negative = False
    index = 0
    text = str(query or "")
    while index < len(text):
        char = text[index]
        if char.isspace() or char in "()":
            index += 1
            continue
        if char == "-":
            pending_negative = True
            index += 1
            continue
        if char == '"':
            closing = text.find('"', index + 1)
            if closing == -1:
                inner = text[index + 1 :]
                index = len(text)
            else:
                inner = text[index + 1 : closing]
                index = closing + 1
            phrase = " ".join(_TOKEN_RE.findall(inner))
            if phrase:
                entries.append((phrase, pending_negative, True))
            pending_negative = False
            continue
        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in '()"':
            index += 1
        token_text = text[start:index]
        tokens = [
            token
            for token in _TOKEN_RE.findall(token_text)
            if token.casefold() not in _OPERATORS
        ]
        if tokens:
            entries.append((" ".join(tokens), pending_negative, False))
        pending_negative = False

    positives = [(phrase, quoted) for phrase, negative, quoted in entries if not negative]
    negatives = [(phrase, quoted) for phrase, negative, quoted in entries if negative]
    filtered_positives = [
        (phrase if quoted else _drop_stopwords(phrase), quoted)
        for phrase, quoted in positives
    ]
    filtered_positives = [item for item in filtered_positives if item[0]]
    # Never let stopword removal erase the whole query: fall back to the
    # original terms when nothing contentful is left.
    if not filtered_positives:
        filtered_positives = positives
    filtered_negatives = [
        (phrase if quoted else _drop_stopwords(phrase), quoted)
        for phrase, quoted in negatives
    ]
    filtered_negatives = [item for item in filtered_negatives if item[0]]
    positive = [_quote_phrase(phrase, scope) for phrase, _quoted in filtered_positives]
    negative = [_quote_phrase(phrase, scope) for phrase, _quoted in filtered_negatives]
    if not positive:
        raise SearchError("Digite uma consulta com pelo menos uma palavra.")
    return _ParsedQuery(tuple(positive), tuple(negative))


def _quote_phrase(phrase: str, scope: str) -> str:
    escaped = phrase.replace('"', '""')
    literal = f'"{escaped}"'
    column = _SCOPE_COLUMNS.get(scope)
    return f"{column} : {literal}" if column else literal


def _positive_expression(phrases: Sequence[str], operator: str, scope: str) -> str:
    del scope
    return f" {operator} ".join(phrases)


def _filter_clauses(query: SearchQuery) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if query.year_min is not None:
        clauses.append("t.year >= ?")
        parameters.append(query.year_min)
    if query.year_max is not None:
        clauses.append("t.year <= ?")
        parameters.append(query.year_max)
    for column, values in (
        ("degree", query.degree),
        ("uf", query.uf),
        ("institution_acronym", query.institution),
        ("great_area", query.great_area),
        ("knowledge_area", query.knowledge_area),
    ):
        if not values:
            continue
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"t.{column} IN ({placeholders})")
        parameters.extend(values)
    return " AND ".join(clauses), parameters


def _clean_values(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    if isinstance(values, str):
        values = [values]
    return tuple(
        str(value).strip() for value in values if value is not None and str(value).strip()
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SearchError("Ano inválido no filtro.") from exc


def _public_row(data: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": data.get("id"),
        "title": data.get("title"),
        "author": data.get("author"),
        "advisors": data.get("advisors"),
        "year": data.get("year"),
        "degree": data.get("degree"),
        "institution": data.get("institution"),
        "institution_acronym": data.get("institution_acronym"),
        "uf": data.get("uf"),
        "program": data.get("program"),
        "great_area": data.get("great_area"),
        "knowledge_area": data.get("knowledge_area"),
        "keywords": data.get("keywords"),
        "url": data.get("url"),
        "snippet": "",
        "score": 0.0,
        "matched_by": [],
    }
    return result


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _terms_from_positives(phrases: Sequence[str]) -> list[str]:
    """Recover raw positive terms (without FTS column prefixes/quotes)."""
    terms: list[str] = []
    for phrase in phrases:
        text = phrase
        if " : " in text:
            text = text.split(" : ", 1)[1]
        text = text.strip()
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            text = text[1:-1].replace('""', '"')
        if text:
            terms.append(text)
    return terms


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Fold text to lowercase without diacritics, keeping source offsets."""
    folded: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFD", char.casefold())
        plain = "".join(part for part in decomposed if not unicodedata.combining(part))
        folded.append(plain)
        mapping.extend([index] * len(plain))
    return "".join(folded), mapping


def _term_pattern(term: str) -> re.Pattern[str] | None:
    """Compile a folded word-sequence pattern for one query term."""
    words = [_fold_token(token) for token in _TOKEN_RE.findall(term)]
    words = [word for word in words if word]
    if not words:
        return None
    return re.compile(
        r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"
    )


def _find_occurrences(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
    """Return non-overlapping ``(start, end)`` spans matching any term."""
    if not text or not terms:
        return []
    folded, mapping = _fold_with_map(text)
    spans: list[tuple[int, int]] = []
    for term in terms:
        pattern = _term_pattern(term)
        if pattern is None:
            continue
        for match in pattern.finditer(folded):
            first, last = match.start(), match.end() - 1
            if first < 0 or last >= len(mapping) or last < first:
                continue
            spans.append((mapping[first], mapping[last] + 1))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if start >= end:
            continue
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))
    return merged


def _shift_start(text: str, position: int) -> int:
    """Move ``position`` forward to the next word boundary."""
    length = len(text)
    if position <= 0:
        return 0
    if text[position - 1].isspace() or not text[position].isalnum():
        return position
    while position < length and not text[position].isspace():
        position += 1
    while position < length and text[position].isspace():
        position += 1
    return position


def _shift_end(text: str, position: int) -> int:
    """Move ``position`` backward to the start of the current word."""
    length = len(text)
    if position >= length:
        return length
    if text[position].isspace() or not text[position - 1].isalnum():
        return position
    while position > 0 and not text[position - 1].isspace():
        position -= 1
    return position


def _window_bounds(
    text: str, spans: Sequence[tuple[int, int]], width: int
) -> tuple[int, int]:
    """Pick the ``width``-char window containing the most occurrences."""
    length = len(text)
    if length <= width:
        return 0, length
    if not spans:
        return 0, _shift_end(text, width)
    candidates = {0}
    for start, _end in spans:
        candidates.add(max(0, min(start - width // 5, length - width)))
        candidates.add(max(0, min(start, length - width)))
    best_start, best_end, best_count = 0, width, -1
    for candidate in sorted(candidates):
        start = _shift_start(text, candidate)
        end = min(length, start + width)
        count = sum(1 for first, last in spans if first >= start and last <= end)
        if count > best_count:
            best_start, best_end, best_count = start, end, count
    return best_start, best_end


def _mark_window(
    text: str, start: int, end: int, spans: Sequence[tuple[int, int]]
) -> str:
    """Insert ``[[``/``]]`` around matches fully inside ``[start, end)``."""
    pieces: list[str] = []
    cursor = start
    for first, last in spans:
        if first < start or last > end:
            continue
        pieces.append(text[cursor:first])
        pieces.append("[[")
        pieces.append(text[first:last])
        pieces.append("]]")
        cursor = last
    pieces.append(text[cursor:end])
    body = "".join(pieces)
    if start > 0:
        body = "…" + body
    if end < len(text):
        body += "…"
    return body


def build_snippet(
    text: str | None,
    terms: Sequence[str],
    width: int = SNIPPET_CHARS,
) -> str:
    """Build a ``[[matched]]`` excerpt without relying on FTS ``snippet()``.

    Matching ignores case and diacritics (NFD), phrases match as word
    sequences, and the excerpt is cut on word boundaries with ellipses.
    """
    if not text:
        return ""
    value = str(text)
    spans = _find_occurrences(value, terms)
    start, end = _window_bounds(value, spans, width)
    return _mark_window(value, start, end, spans)


def _snippet_for(source: Mapping[str, Any], terms: Sequence[str]) -> str:
    """Return a truecased snippet for a result row."""
    raw = source.get("abstract") or source.get("keywords") or source.get("title") or ""
    snippet = build_snippet(str(raw), terms)
    # A window that starts at the very beginning of the text is a sentence
    # start; one that starts mid-text is prefixed with an ellipsis.
    at_start = not snippet.lstrip().startswith(("…", "..."))
    return truecase(snippet, capitalize_first=at_start)


def _ordered_matched(values: set[str]) -> list[str]:
    return [item for item in ("texto", "significado") if item in values]


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "MODES",
    "RRF_K",
    "SCOPES",
    "SORTS",
    "CatalogSearch",
    "SearchError",
    "SearchQuery",
    "SearchResult",
    "TEXT_CANDIDATES",
    "TEXT_COUNT_LIMIT",
    "TEXT_TIMEOUT_SECONDS",
    "SEMANTIC_CANDIDATES",
    "TextQueryTimeout",
    "parse_fts_query",
]
