"""Build a local SQLite/FTS5 catalog and NumPy vector index from Parquet."""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from array import array
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from buscateses.catalog.manifest import Manifest
from buscateses.embeddings.base import truncate_normalize
from buscateses.textcase import name_case

CATALOG_DB = "catalog.sqlite"
VECTORS_FILE = "vectors.npy"
FILTERS_FILE = "filters.npz"
BUILD_MARKER = "BUILD_OK"
MANIFEST_FILE = "manifest.json"
VECTOR_DIMS = 256
BATCH_SIZE = 8192
#: On-disk layout version.  1 = plain text abstracts + float32 vectors,
#: 2 = zlib-compressed abstracts + float16 vectors + contentless FTS5.
FORMAT_VERSION = 2
VECTOR_DTYPE = np.float16
ZLIB_LEVEL = 6
#: VACUUM is only worth its cost when at least this much space is free.
VACUUM_MIN_BYTES = 32 * 1024 * 1024

_METADATA_COLUMNS = (
    "id",
    "year",
    "defense_date",
    "title",
    "author",
    "advisors",
    "degree",
    "institution",
    "institution_acronym",
    "uf",
    "region",
    "program",
    "program_code",
    "great_area",
    "knowledge_area",
    "evaluation_area",
    "concentration_area",
    "research_line",
    "keywords",
    "keywords_en",
    "abstract",
    "abstract_en",
    "language",
    "pages",
    "library",
    "url",
    "source",
)
_TEXT_COLUMNS = tuple(column for column in _METADATA_COLUMNS if column not in {"year", "pages"})
_FACET_COLUMNS = ("degree", "uf", "great_area", "knowledge_area", "institution_acronym")
_EMPTY_VALUES = {"", "-", "nao informado", "não informado", "null", "none", "nan"}
_WHITESPACE = re.compile(r"\s+")
#: Abstracts are stored compressed to keep the release small.
_COMPRESSED_COLUMNS = {"abstract": "abstract_z", "abstract_en": "abstract_en_z"}
_INSERT_COLUMNS = tuple(
    _COMPRESSED_COLUMNS.get(column, column) for column in _METADATA_COLUMNS
)
_FTS_COLUMNS = (
    "title",
    "keywords",
    "abstract",
    "author",
    "advisors",
    "program",
    "institution",
)
_FTS_WEIGHTS = (8.0, 4.0, 1.0, 3.0, 2.0, 1.0, 1.0)


def pack_text(value: str | None) -> bytes | None:
    """Compress ``value`` with zlib, preserving ``None`` as SQL ``NULL``."""
    if value is None:
        return None
    return zlib.compress(str(value).encode("utf-8"), ZLIB_LEVEL)


def unpack_text(value: bytes | bytearray | memoryview | str | None) -> str | None:
    """Expand a value produced by :func:`pack_text`.

    Plain strings are returned unchanged so that format-1 releases and
    in-memory records can flow through the same code path.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return zlib.decompress(bytes(value)).decode("utf-8")


class BuildError(RuntimeError):
    """Raised when source files cannot be turned into a catalog."""


@dataclass(frozen=True)
class BuildResult:
    """Summary of a completed catalog build."""

    version: str
    records: int
    missing_vectors: int
    built_at: str
    release_dir: Path


def build_catalog(
    manifest: Manifest,
    download_dir: str | Path,
    release_dir: str | Path,
    *,
    ctx: Any | None = None,
) -> BuildResult:
    """Build SQLite, FTS5, vectors and filter masks into ``release_dir``."""
    downloads = Path(download_dir)
    release = Path(release_dir)
    if release.exists() and any(release.iterdir()):
        raise BuildError("O diretório de destino do catálogo não está vazio.")
    release.mkdir(parents=True, exist_ok=True)

    metadata_files = [item for item in manifest.files if item.kind == "metadata"]
    total_rows = max(manifest.records, sum(item.rows for item in metadata_files))
    database_path = release / CATALOG_DB
    vectors_path = release / VECTORS_FILE
    filters_path = release / FILTERS_FILE

    connection = sqlite3.connect(database_path)
    try:
        _configure_build_connection(connection)
        _create_schema(connection)
        ids: list[str] = []
        id_to_row: dict[str, int] = {}
        year_values = array("h")
        code_arrays = {kind: array("i") for kind in _FACET_COLUMNS}
        vocabularies: dict[str, list[str]] = {kind: [""] for kind in _FACET_COLUMNS}
        vocabulary_maps: dict[str, dict[str, int]] = {kind: {} for kind in _FACET_COLUMNS}
        facet_counts = {kind: Counter() for kind in _FACET_COLUMNS}
        year_counts: Counter[int] = Counter()
        institution_names: dict[str, Counter[str]] = {}
        processed = 0

        for file_item in metadata_files:
            source = downloads / file_item.path
            if not source.is_file():
                raise BuildError(f"Arquivo de metadados não encontrado: {file_item.path}")
            try:
                parquet = pq.ParquetFile(source)
            except Exception as exc:
                raise BuildError(f"Não foi possível abrir {file_item.path}.") from exc
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                _check_cancelled(ctx)
                data = batch.to_pydict()
                missing_columns = [name for name in ("id",) if name not in data]
                if missing_columns:
                    raise BuildError("Os metadados não contêm a coluna obrigatória 'id'.")
                rows = _metadata_rows(data)
                for row in rows:
                    identifier = row["id"]
                    if identifier is None:
                        raise BuildError("Há registros sem identificador nos metadados.")
                    if identifier in id_to_row:
                        raise BuildError(f"Identificador repetido nos metadados: {identifier}")
                    try:
                        cursor = connection.execute(
                            f"INSERT INTO theses ({', '.join(_INSERT_COLUMNS)}) "
                            f"VALUES ({', '.join('?' for _ in _INSERT_COLUMNS)})",
                            [
                                pack_text(row[column])
                                if column in _COMPRESSED_COLUMNS
                                else row[column]
                                for column in _METADATA_COLUMNS
                            ],
                        )
                    except sqlite3.IntegrityError as exc:
                        raise BuildError(f"Identificador repetido nos metadados: {identifier}") from exc
                    row_number = int(cursor.lastrowid)
                    id_to_row[identifier] = row_number
                    ids.append(identifier)
                    _append_filter_values(
                        row,
                        row_number,
                        year_values,
                        code_arrays,
                        vocabularies,
                        vocabulary_maps,
                        facet_counts,
                        year_counts,
                        institution_names,
                    )
                    processed += 1
                _report(
                    ctx,
                    stage="build_db",
                    message="Montando o banco de dados do catálogo…",
                    done=processed,
                    total=total_rows,
                )
        if processed == 0:
            raise BuildError("O catálogo não possui registros.")

        _report(
            ctx,
            stage="build_index",
            message="Preparando o índice de busca…",
            done=0,
            total=processed,
        )
        _populate_fts(connection, processed, ctx=ctx)
        connection.commit()

        valid_vectors = _write_vectors(
            manifest,
            downloads,
            vectors_path,
            len(ids),
            id_to_row,
            ctx=ctx,
        )
        missing_vectors = len(ids) - int(valid_vectors.sum())
        _insert_facets(connection, facet_counts, year_counts, institution_names)
        _write_filters(
            filters_path,
            year_values,
            code_arrays,
            vocabularies,
            has_vector=valid_vectors,
        )
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        meta = {
            "version": manifest.version,
            "records": str(len(ids)),
            "built_at": built_at,
            "format_version": str(FORMAT_VERSION),
            "vector_dtype": np.dtype(VECTOR_DTYPE).name,
            "missing_vectors": str(missing_vectors),
            "years": f"{int(min(year_values)) if year_values else 0},"
            f"{int(max(year_values)) if year_values else 0}",
            "dimensions": str(VECTOR_DIMS),
        }
        connection.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", meta.items())
        connection.execute("INSERT INTO theses_fts(theses_fts) VALUES('optimize')")
        connection.execute("ANALYZE")
        connection.commit()
        _vacuum_if_useful(connection, database_path)
        (release / MANIFEST_FILE).write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _report(
            ctx,
            stage="finalize",
            message="Finalizando o catálogo…",
            done=processed,
            total=processed,
        )
        return BuildResult(
            version=manifest.version,
            records=len(ids),
            missing_vectors=missing_vectors,
            built_at=built_at,
            release_dir=release,
        )
    finally:
        connection.close()


def _configure_build_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA page_size=4096")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-131072")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE theses(
            rowid INTEGER PRIMARY KEY,
            id TEXT UNIQUE NOT NULL,
            year INTEGER,
            defense_date TEXT,
            title TEXT,
            author TEXT,
            advisors TEXT,
            degree TEXT,
            institution TEXT,
            institution_acronym TEXT,
            uf TEXT,
            region TEXT,
            program TEXT,
            program_code TEXT,
            great_area TEXT,
            knowledge_area TEXT,
            evaluation_area TEXT,
            concentration_area TEXT,
            research_line TEXT,
            keywords TEXT,
            keywords_en TEXT,
            abstract_z BLOB,
            abstract_en_z BLOB,
            language TEXT,
            pages INTEGER,
            library TEXT,
            url TEXT,
            source TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE theses_fts USING fts5(
            title,
            keywords,
            abstract,
            author,
            advisors,
            program,
            institution,
            content='',
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    connection.execute(
        "CREATE TABLE facets(kind TEXT NOT NULL, value TEXT NOT NULL, "
        "label TEXT NOT NULL, count INTEGER NOT NULL)"
    )
    connection.execute("CREATE INDEX idx_facets_kind ON facets(kind, count DESC)")
    connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for column in (
        "year",
        "degree",
        "uf",
        "institution_acronym",
        "great_area",
        "knowledge_area",
    ):
        connection.execute(f"CREATE INDEX idx_theses_{column} ON theses({column})")


def _metadata_rows(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    lengths = {len(values) for values in data.values()}
    if len(lengths) > 1:
        raise BuildError("As colunas dos metadados têm tamanhos diferentes.")
    count = lengths.pop() if lengths else 0
    for index in range(count):
        row: dict[str, Any] = {}
        for column in _METADATA_COLUMNS:
            values = data.get(column)
            raw = values[index] if values is not None else None
            if column == "year":
                parsed_year = _coerce_int(raw)
                if parsed_year is not None and not -32768 <= parsed_year <= 32767:
                    raise BuildError(f"Ano fora do intervalo suportado: {parsed_year}")
                row[column] = parsed_year
            elif column == "pages":
                row[column] = _coerce_int(raw)
            elif column == "id":
                row[column] = _clean_identifier(raw)
            else:
                row[column] = _clean_text(raw)
        yield row


def _append_filter_values(
    row: dict[str, Any],
    row_number: int,
    year_values: array,
    code_arrays: dict[str, array],
    vocabularies: dict[str, list[str]],
    vocabulary_maps: dict[str, dict[str, int]],
    facet_counts: dict[str, Counter[str]],
    year_counts: Counter[int],
    institution_names: dict[str, Counter[str]],
) -> None:
    if row_number - 1 != len(year_values):
        raise BuildError("A ordem dos registros do catálogo ficou inconsistente.")
    year = row.get("year")
    year_values.append(int(year) if year is not None else 0)
    if year is not None:
        year_counts[int(year)] += 1
    for kind in _FACET_COLUMNS:
        raw_value = row.get(kind)
        value = str(raw_value) if raw_value not in (None, "") else ""
        code = 0
        if value:
            code = vocabulary_maps[kind].get(value, 0)
            if not code:
                code = len(vocabularies[kind])
                vocabularies[kind].append(value)
                vocabulary_maps[kind][value] = code
            facet_counts[kind][value] += 1
        code_arrays[kind].append(code)
        if kind == "institution_acronym" and row.get("institution"):
            institution_names.setdefault(value, Counter())[str(row["institution"])] += 1


def _populate_fts(
    connection: sqlite3.Connection,
    total: int,
    *,
    ctx: Any | None,
) -> None:
    """Fill the contentless FTS5 index with decompressed abstracts."""
    reader = connection.cursor()
    writer = connection.cursor()
    written = 0
    try:
        reader.execute(
            "SELECT rowid, title, keywords, abstract_z, author, advisors, program, institution "
            "FROM theses ORDER BY rowid"
        )
        while True:
            _check_cancelled(ctx)
            batch = reader.fetchmany(BATCH_SIZE)
            if not batch:
                break
            rows = [
                (
                    int(row[0]),
                    row[1],
                    row[2],
                    unpack_text(row[3]),
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                )
                for row in batch
            ]
            writer.executemany(
                "INSERT INTO theses_fts(rowid, title, keywords, abstract, author, advisors, "
                "program, institution) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            written += len(rows)
            _report(
                ctx,
                stage="build_index",
                message="Montando o índice de busca textual…",
                done=written,
                total=total,
            )
    finally:
        reader.close()
        writer.close()


def _write_vectors(
    manifest: Manifest,
    downloads: Path,
    vectors_path: Path,
    count: int,
    id_to_row: dict[str, int],
    *,
    ctx: Any | None,
) -> np.ndarray:
    """Write ``[count, 256]`` float16 vectors and return a valid-row mask."""
    vectors = np.lib.format.open_memmap(
        vectors_path, mode="w+", dtype=VECTOR_DTYPE, shape=(count, VECTOR_DIMS)
    )
    seen = np.zeros(count, dtype=bool)
    try:
        for file_item in (item for item in manifest.files if item.kind == "embeddings-256"):
            source = downloads / file_item.path
            if not source.is_file():
                raise BuildError(f"Arquivo de embeddings não encontrado: {file_item.path}")
            try:
                parquet = pq.ParquetFile(source)
            except Exception as exc:
                raise BuildError(f"Não foi possível abrir {file_item.path}.") from exc
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                _check_cancelled(ctx)
                data = batch.to_pydict()
                if "id" not in data or "embedding" not in data:
                    raise BuildError(
                        "Os embeddings precisam das colunas 'id' e 'embedding'."
                    )
                for identifier, embedding in zip(data["id"], data["embedding"], strict=True):
                    if identifier is None or embedding is None:
                        continue
                    row_number = id_to_row.get(str(identifier))
                    if row_number is None:
                        continue
                    index = row_number - 1
                    if seen[index]:
                        raise BuildError(f"Embedding repetido para o registro {identifier}.")
                    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
                    if vector.size < VECTOR_DIMS:
                        raise BuildError(
                            f"Embedding com dimensões insuficientes para {identifier}."
                        )
                    normalized = truncate_normalize(vector, VECTOR_DIMS)
                    vectors[index] = normalized.astype(VECTOR_DTYPE)
                    seen[index] = True
                _report(
                    ctx,
                    stage="build_index",
                    message="Indexando os vetores do catálogo…",
                    done=int(seen.sum()),
                    total=count,
                )
        vectors.flush()
        valid = np.any(vectors != 0, axis=1)
    finally:
        del vectors
    return valid


def _insert_facets(
    connection: sqlite3.Connection,
    facet_counts: dict[str, Counter[str]],
    year_counts: Counter[int],
    institution_names: dict[str, Counter[str]],
) -> None:
    rows: list[tuple[str, str, str, int]] = []
    for kind in _FACET_COLUMNS:
        for value, count in sorted(
            facet_counts[kind].items(), key=lambda item: (-item[1], item[0])
        ):
            label = value
            if kind in {"degree", "great_area", "knowledge_area"}:
                label = name_case(value)
            elif kind == "institution_acronym":
                names = institution_names.get(value)
                if names:
                    label = f"{value} — {name_case(names.most_common(1)[0][0])}"
            rows.append((kind, value, label, int(count)))
    for year, count in sorted(year_counts.items()):
        rows.append(("year", str(year), str(year), int(count)))
    connection.executemany(
        "INSERT INTO facets(kind, value, label, count) VALUES(?, ?, ?, ?)", rows
    )
    connection.commit()


def _write_filters(
    path: Path,
    year_values: array,
    code_arrays: dict[str, array],
    vocabularies: dict[str, list[str]],
    *,
    has_vector: np.ndarray,
) -> None:
    payload: dict[str, np.ndarray] = {
        "year": np.asarray(year_values, dtype=np.int16),
    }
    for kind, values in code_arrays.items():
        payload[kind] = np.asarray(values, dtype=np.int32)
        payload[f"{kind}_vocab"] = np.asarray(vocabularies[kind], dtype=np.str_)
    payload["has_vector"] = np.asarray(has_vector, dtype=bool)
    np.savez_compressed(path, **payload)


def _vacuum_if_useful(connection: sqlite3.Connection, database_path: Path) -> bool:
    """Run ``VACUUM`` only when a meaningful amount of space is free."""
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    except sqlite3.Error:
        return False
    if page_size * free_pages < VACUUM_MIN_BYTES:
        return False
    before = database_path.stat().st_size if database_path.is_file() else 0
    connection.execute("VACUUM")
    after = database_path.stat().st_size if database_path.is_file() else 0
    return after < before


def _clean_identifier(value: Any) -> str | None:
    clean = _clean_text(value)
    return clean


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    clean = _WHITESPACE.sub(" ", str(value).strip())
    if clean.casefold() in _EMPTY_VALUES:
        return None
    return clean


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_cancelled(ctx: Any | None) -> None:
    if ctx is not None:
        ctx.check_cancelled()


def _report(ctx: Any | None, *, stage: str, message: str, done: int, total: int) -> None:
    if ctx is None:
        return
    progress = (done / total) if total else None
    ctx.update(
        stage=stage,
        message=message,
        progress=progress,
        bytes_done=done,
        bytes_total=total,
    )


def write_build_marker(release_dir: str | Path, payload: dict[str, Any]) -> Path:
    """Write ``BUILD_OK`` atomically inside a release directory."""
    destination = Path(release_dir) / BUILD_MARKER
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = [
    "BATCH_SIZE",
    "BUILD_MARKER",
    "BuildError",
    "BuildResult",
    "CATALOG_DB",
    "FILTERS_FILE",
    "FORMAT_VERSION",
    "MANIFEST_FILE",
    "VECTORS_FILE",
    "VECTOR_DIMS",
    "VECTOR_DTYPE",
    "build_catalog",
    "pack_text",
    "unpack_text",
    "write_build_marker",
]
