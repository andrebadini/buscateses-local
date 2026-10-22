#!/usr/bin/env python3
"""Assemble the public release from metadata parts and embedding units.

Produces ``metadata/``, ``embeddings-768/`` and ``embeddings-256/`` Parquet
files that are aligned row by row, plus ``manifest.json`` (SPEC §3.3) and the
Hugging Face dataset card (``README.md`` and SVG charts, via ``card.py``).
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import card as dataset_card
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from common import (
    APP_DIMS,
    DOCUMENT_TEMPLATE,
    EMBEDDING_256_SCHEMA,
    EMBEDDING_MODEL,
    EMBEDDING_SCHEMA,
    FULL_DIMS,
    METADATA_SCHEMA,
    ONNX_REPO,
    PROBE_TEXT,
    QUERY_TEMPLATE,
    SOURCE_LICENSE,
    SOURCE_NAME,
    SOURCE_URL,
    setup_logging,
    sha256_file,
)

LOGGER = setup_logging("finalize")
# Versão mínima do app que lê este catálogo (o esquema não mudou desde a 1.0.0).
APP_VERSION = "1.0.0"
DATASET_REPO = "andrebadini/catalogo-teses-capes"
APP_URL = "https://github.com/andrebadini/buscateses-local"

#: Recent years are published from two additional public sources.
CAPES_CATALOGUE_NAME = "Catálogo de Teses e Dissertações da CAPES"
CAPES_CATALOGUE_URL = "https://catalogodeteses.capes.gov.br/catalogo-teses/"
CAPES_CATALOGUE_LICENSE_NOTE = (
    "Metadados públicos do Catálogo de Teses e Dissertações da CAPES; "
    "consulte os termos de uso da fonte."
)
BDTD_NAME = "BDTD (IBICT)"
BDTD_URL = "https://bdtd.ibict.br/"
BDTD_LICENSE_NOTE = (
    "Metadados públicos da Biblioteca Digital Brasileira de Teses e Dissertações "
    "(IBICT) e das bibliotecas digitais participantes; consulte a licença de cada "
    "repositório antes de reutilizar o texto completo."
)
CAPES_OPEN_DATA_LICENSE_NOTE = (
    "Dados abertos da CAPES sob licença Creative Commons Atribuição (CC-BY); "
    "atribuição à CAPES — Catálogo de Teses e Dissertações."
)
#: Target number of rows per published metadata (and aligned embedding) part.
RELEASE_PART_ROWS = 250_000


class MissingEmbeddingsError(RuntimeError):
    """Some metadata ids have no embedding and --allow-missing was not given."""


def _directories(value: Path | Sequence[Path]) -> list[Path]:
    """Accept one directory or a sequence of directories."""
    if isinstance(value, (str, Path)):
        return [Path(value)]
    directories = [Path(item) for item in value]
    if not directories:
        raise ValueError("Informe ao menos um diretório")
    return directories


def _metadata_parts(metadata_dir: Path) -> list[Path]:
    parts = sorted(Path(metadata_dir).glob("*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Nenhuma parte de metadados em {metadata_dir}")
    return parts


def _all_metadata_parts(metadata_dirs: Iterable[Path]) -> list[Path]:
    parts = [path for directory in metadata_dirs for path in _metadata_parts(directory)]
    if not parts:
        raise FileNotFoundError("Nenhuma parte de metadados nos diretórios informados")
    return parts


def _embedding_units(emb_dir: Path) -> list[Path]:
    def unit_number(path: Path) -> int:
        match = re.search(r"unit-(\d+)", path.name)
        return int(match.group(1)) if match else -1

    units = sorted(Path(emb_dir).glob("unit-*.parquet"), key=unit_number)
    if not units:
        raise FileNotFoundError(f"Nenhuma unidade de embeddings em {emb_dir}")
    return units


def _all_embedding_units(emb_dirs: Iterable[Path]) -> list[Path]:
    units = [path for directory in emb_dirs for path in _embedding_units(directory)]
    if not units:
        raise FileNotFoundError("Nenhuma unidade de embeddings nos diretórios informados")
    return units


def _load_embeddings(emb_dir: Path | Sequence[Path]) -> tuple[np.ndarray, dict[str, int]]:
    """Load every unit into one float16 matrix plus an id → row index map."""
    units = _all_embedding_units(_directories(emb_dir))
    total = sum(pq.ParquetFile(path).metadata.num_rows for path in units)
    matrix = np.empty((total, FULL_DIMS), dtype=np.float16)
    identifiers: list[str] = []
    position = 0
    for path in units:
        table = pq.read_table(path, columns=["id", "embedding"])
        if table.num_rows == 0:
            continue
        embedding_field = table.schema.field("embedding")
        if not pa.types.is_fixed_size_list(embedding_field.type) or embedding_field.type.list_size != FULL_DIMS:
            raise ValueError(f"{path.name}: embedding deve ser fixed_size_list<...>[{FULL_DIMS}]")
        column = table.column("embedding").combine_chunks()
        values = column.values.to_numpy(zero_copy_only=False)
        block = np.asarray(values, dtype=np.float16).reshape(table.num_rows, FULL_DIMS)
        matrix[position : position + table.num_rows] = block
        identifiers.extend(table.column("id").to_pylist())
        position += table.num_rows
    matrix = matrix[:position]
    index: dict[str, int] = {}
    for row, identifier in enumerate(identifiers):
        if identifier in index:
            raise ValueError(f"Embedding duplicado para o id {identifier}")
        index[identifier] = row
    return matrix, index


def _write_embedding_part(path: Path, identifiers: Sequence[str], matrix: np.ndarray) -> None:
    """Write one aligned embeddings part atomically."""
    block = np.ascontiguousarray(matrix, dtype=np.float16)
    if block.ndim != 2 or block.shape[1] not in (APP_DIMS, FULL_DIMS):
        raise ValueError(f"Formato de embeddings inválido: {block.shape}")
    dimensions = block.shape[1]
    schema = EMBEDDING_SCHEMA if dimensions == FULL_DIMS else EMBEDDING_256_SCHEMA
    embedding = pa.FixedSizeListArray.from_arrays(
        pa.array(block.reshape(-1), type=pa.float16()), dimensions
    )
    table = pa.Table.from_arrays(
        [pa.array(identifiers, type=pa.string()), embedding], schema=schema
    )
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd", row_group_size=50_000)
    os.replace(temporary, path)


def _matryoshka_256(matrix: np.ndarray) -> np.ndarray:
    """Take the first 256 dims and re-normalise them (Matryoshka)."""
    reduced = np.asarray(matrix[:, :APP_DIMS], dtype=np.float32)
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    normalized = np.divide(
        reduced,
        np.maximum(norms, 1e-12),
        out=np.zeros_like(reduced),
        where=norms > 0,
    )
    return normalized.astype(np.float16)


def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Sort metadata rows by ``(year, id)``; missing values sort first."""
    year = row.get("year")
    return (int(year) if year is not None else 0, str(row.get("id") or ""))


def _iter_sorted_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Read one metadata part and yield its rows in ``(year, id)`` order."""
    table = pq.read_table(path)
    order = pc.sort_indices(
        table, sort_keys=[("year", "ascending"), ("id", "ascending")]
    )
    yield from table.take(order).to_pylist()


def _write_metadata_part(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """Write one metadata part atomically with the public schema."""
    table = pa.Table.from_pylist(list(rows), schema=METADATA_SCHEMA)
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd", row_group_size=50_000)
    os.replace(temporary, path)


def _merge_metadata(
    metadata_dirs: Sequence[Path],
    out_dir: Path,
) -> tuple[list[list[str]], list[int], Counter[str], dict[str, list[int]]]:
    """Merge every metadata directory into aligned, globally sorted parts.

    Returns the identifiers of each output part, all year values, the number
    of records per source and the year range of each source.
    """
    parts = _all_metadata_parts(metadata_dirs)
    for index, path in enumerate(parts):
        schema = pq.ParquetFile(path).schema_arrow
        if schema.names != METADATA_SCHEMA.names:
            raise ValueError(f"{path.name}: schema de metadados inesperado")
        if index >= 1000:
            raise ValueError("Há mais de 1000 partes de metadados")

    streams = [_iter_sorted_rows(path) for path in parts]
    # A single directory keeps its part boundaries (and therefore the file
    # numbering of its aligned embedding units).  Several directories are
    # merged into globally sorted parts of at most RELEASE_PART_ROWS rows.
    multi = len(metadata_dirs) > 1
    row_groups: Iterable[Iterable[dict[str, Any]]] = (
        (heapq.merge(*streams, key=_sort_key),)
        if multi
        else streams
    )
    identifiers_by_part: list[list[str]] = []
    year_values: list[int] = []
    source_counts: Counter[str] = Counter()
    source_years: dict[str, list[int]] = {}
    last_identifier: str | None = None
    part_number = 0

    for group in row_groups:
        buffer: list[dict[str, Any]] = []
        for row in group:
            identifier = row.get("id")
            if identifier is not None and identifier == last_identifier:
                raise ValueError(f"Metadado duplicado para o id {identifier}")
            last_identifier = identifier
            buffer.append(row)
            year = row.get("year")
            if year is not None:
                year_values.append(int(year))
            source = str(row.get("source") or "")
            source_counts[source] += 1
            if year is not None:
                bounds = source_years.setdefault(source, [int(year), int(year)])
                bounds[0] = min(bounds[0], int(year))
                bounds[1] = max(bounds[1], int(year))
            if multi and len(buffer) == RELEASE_PART_ROWS:
                _flush_metadata_part(out_dir, part_number, buffer, identifiers_by_part)
                part_number += 1
                buffer = []
        if buffer or not multi:
            _flush_metadata_part(out_dir, part_number, buffer, identifiers_by_part)
            part_number += 1

    if not year_values:
        raise ValueError("Não foi possível determinar o intervalo de anos")
    return identifiers_by_part, year_values, source_counts, source_years


def _flush_metadata_part(
    out_dir: Path,
    part_number: int,
    rows: list[dict[str, Any]],
    identifiers_by_part: list[list[str]],
) -> None:
    path = out_dir / "metadata" / f"part-{part_number:03d}.parquet"
    _write_metadata_part(path, rows)
    identifiers_by_part.append([str(row.get("id")) for row in rows])


def _manifest_sources(
    source_counts: Counter[str], source_years: dict[str, list[int]]
) -> list[dict[str, Any]]:
    """Describe each data source for the manifest."""
    recent_sources = {"bdtd", "capes-catalogo"}
    entries: list[dict[str, Any]] = []
    open_data = sum(
        count for source, count in source_counts.items() if source not in recent_sources
    )
    if open_data:
        entries.append(
            {
                "name": SOURCE_NAME,
                "url": SOURCE_URL,
                "records": open_data,
                "license_note": CAPES_OPEN_DATA_LICENSE_NOTE,
            }
        )
    if source_counts.get("capes-catalogo"):
        entries.append(
            {
                "name": CAPES_CATALOGUE_NAME,
                "url": CAPES_CATALOGUE_URL,
                "records": source_counts["capes-catalogo"],
                "license_note": CAPES_CATALOGUE_LICENSE_NOTE,
            }
        )
    if source_counts.get("bdtd"):
        entries.append(
            {
                "name": BDTD_NAME,
                "url": BDTD_URL,
                "records": source_counts["bdtd"],
                "license_note": BDTD_LICENSE_NOTE,
            }
        )
    if not entries:
        entries.append(
            {
                "name": SOURCE_NAME,
                "url": SOURCE_URL,
                "records": sum(source_counts.values()),
                "license_note": CAPES_OPEN_DATA_LICENSE_NOTE,
            }
        )
    return entries


def compute_probe(
    backend_name: str,
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str = "embeddinggemma",
    concurrency: int = 3,
    batch_size: int = 32,
    device: str = "cpu",
    model_name: str = EMBEDDING_MODEL,
    timeout: float = 120.0,
) -> list[float]:
    """Embed the manifest probe query and return the first 16 values."""
    from embed import create_backend

    backend = create_backend(
        backend_name,
        url=url,
        api_key=api_key,
        model=model,
        concurrency=concurrency,
        batch_size=batch_size,
        device=device,
        model_name=model_name,
        timeout=timeout,
    )
    vector = backend.embed_texts([PROBE_TEXT])[0]
    return [float(value) for value in vector[:16]]


def _manifest_files(out_dir: Path) -> list[dict[str, Any]]:
    """Describe the Parquet files of the release (SPEC §3.3)."""
    entries: list[dict[str, Any]] = []
    for kind in ("metadata", "embeddings-256", "embeddings-768"):
        for path in sorted((out_dir / kind).glob("*.parquet")):
            entries.append(
                {
                    "path": f"{kind}/{path.name}",
                    "kind": kind,
                    "rows": pq.ParquetFile(path).metadata.num_rows,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return entries


def finalize_release(
    metadata_dir: Path | Sequence[Path],
    emb_dir: Path | Sequence[Path],
    out_dir: Path,
    version: str,
    *,
    allow_missing: bool = False,
    probe_vector: Sequence[float] | None = None,
    probe_backend: str = "openai",
    probe_url: str | None = None,
    probe_api_key: str | None = None,
    probe_model: str = "embeddinggemma",
    probe_device: str = "cpu",
    probe_batch: int = 32,
    probe_concurrency: int = 3,
) -> dict[str, Any]:
    """Build a release directory and return its manifest.

    *metadata_dir* and *emb_dir* may be a single directory or a sequence of
    directories.  Metadata from every directory is merged and re-sorted by
    ``(year, id)`` before being split into aligned output parts.
    """
    metadata_dirs = _directories(metadata_dir)
    emb_dirs = _directories(emb_dir)
    out_dir = Path(out_dir)
    parts = _all_metadata_parts(metadata_dirs)

    for index, path in enumerate(parts):
        schema = pq.ParquetFile(path).schema_arrow
        if schema.names != METADATA_SCHEMA.names:
            raise ValueError(f"{path.name}: schema de metadados inesperado")
        if index >= 1000:
            raise ValueError("Há mais de 1000 partes de metadados")

    embeddings, index_by_id = _load_embeddings(emb_dirs)
    LOGGER.info(
        "Carregados %d embeddings de %d unidade(s)",
        len(index_by_id),
        len(_all_embedding_units(emb_dirs)),
    )

    missing = 0
    for path in parts:
        identifiers = pq.read_table(path, columns=["id"]).column("id").to_pylist()
        missing += sum(1 for identifier in identifiers if identifier not in index_by_id)
    if missing and not allow_missing:
        raise MissingEmbeddingsError(
            f"{missing} registro(s) de metadados sem embedding; use --allow-missing para publicar"
        )

    if out_dir.exists() and (out_dir / "manifest.json").exists():
        raise FileExistsError(
            f"{out_dir} já contém uma release; remova o diretório antes de refazer"
        )
    for kind in ("metadata", "embeddings-256", "embeddings-768"):
        (out_dir / kind).mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*/*.parquet"):
        stale.unlink()

    identifiers_by_part, year_values, source_counts, source_years = _merge_metadata(
        metadata_dirs, out_dir
    )
    min_year, max_year = min(year_values), max(year_values)
    records = sum(len(identifiers) for identifiers in identifiers_by_part)

    for index, identifiers in enumerate(identifiers_by_part):
        indices = np.fromiter(
            (index_by_id.get(identifier, -1) for identifier in identifiers),
            dtype=np.int64,
            count=len(identifiers),
        )
        matrix = np.zeros((len(identifiers), FULL_DIMS), dtype=np.float16)
        valid = indices >= 0
        if valid.any():
            matrix[valid] = embeddings[indices[valid]]
        _write_embedding_part(out_dir / "embeddings-768" / f"part-{index:03d}.parquet", identifiers, matrix)
        _write_embedding_part(
            out_dir / "embeddings-256" / f"part-{index:03d}.parquet",
            identifiers,
            _matryoshka_256(matrix),
        )

    sources = _manifest_sources(source_counts, source_years)

    if probe_vector is None:
        probe_vector = compute_probe(
            probe_backend,
            url=probe_url,
            api_key=probe_api_key,
            model=probe_model,
            device=probe_device,
            batch_size=probe_batch,
            concurrency=probe_concurrency,
        )
    probe_head = [float(value) for value in list(probe_vector)[:16]]
    if len(probe_head) != 16:
        raise ValueError("O vetor de sonda precisa ter pelo menos 16 dimensões")

    manifest: dict[str, Any] = {
        "schema": 1,
        "version": version,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "revision": version,
        "records": records,
        "years": [min_year, max_year],
        "source": {
            "name": SOURCE_NAME,
            "url": SOURCE_URL,
            "license": SOURCE_LICENSE,
        },
        "sources": sources,
        "embedding": {
            "model": EMBEDDING_MODEL,
            "onnx_repo": ONNX_REPO,
            "app_dims": APP_DIMS,
            "full_dims": FULL_DIMS,
            "document_template": DOCUMENT_TEMPLATE,
            "query_template": QUERY_TEMPLATE,
            "probe": {"text": PROBE_TEXT, "vector_768_head": probe_head},
        },
        "files": _manifest_files(out_dir),
        "min_app_version": APP_VERSION,
    }
    if allow_missing:
        manifest["missing_embeddings"] = missing

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Card (README.md + SVG charts) built from the files just written.  The PNGs
    # are rendered afterwards with ``card.py --release`` (needs Playwright).
    dataset_card.build(out_dir, png=False, refresh=True)
    LOGGER.info(
        "Release %s pronta: %d registro(s), %d arquivo(s), %d embedding(s) ausente(s)",
        version,
        records,
        len(manifest["files"]),
        missing,
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Monta a release pública do dataset")
    parser.add_argument(
        "--metadata",
        type=Path,
        action="append",
        required=True,
        help="diretório de metadados; repita para combinar mais de um",
    )
    parser.add_argument(
        "--emb",
        type=Path,
        action="append",
        required=True,
        help="diretório de embeddings; repita para combinar mais de um",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--probe-backend", choices=("openai", "st"), default="openai")
    parser.add_argument("--probe-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--probe-api-key-env", default="TEI_API_KEY")
    parser.add_argument("--probe-model", default="embeddinggemma")
    parser.add_argument("--probe-device", choices=("mps", "cuda", "cpu"), default="cpu")
    parser.add_argument("--probe-batch", type=int, default=32)
    parser.add_argument("--probe-concurrency", type=int, default=3)
    arguments = parser.parse_args(argv)

    api_key = (
        os.environ.get(arguments.probe_api_key_env)
        if arguments.probe_backend == "openai"
        else None
    )
    finalize_release(
        arguments.metadata,
        arguments.emb,
        arguments.out,
        arguments.version,
        allow_missing=arguments.allow_missing,
        probe_backend=arguments.probe_backend,
        probe_url=arguments.probe_url,
        probe_api_key=api_key,
        probe_model=arguments.probe_model,
        probe_device=arguments.probe_device,
        probe_batch=arguments.probe_batch,
        probe_concurrency=arguments.probe_concurrency,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
