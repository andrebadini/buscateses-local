#!/usr/bin/env python3
"""Normalise the raw CAPES CSVs into the public metadata Parquet schema.

Two source schemas are supported (SPEC §3.1): the legacy ``dados_YYYY.csv``
files (1987–2012) and the modern ``br-capes-btd-*.csv`` files (2013+).  Column
mapping is always by header name; forbidden personal-data columns are never
copied to the output.

To keep memory bounded on a 16 GB machine, each CSV is first converted to an
intermediate Parquet file under ``work/stage``.  DuckDB then deduplicates by
``id`` and writes the final sorted parts.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from common import (
    LEGACY_COLUMNS,
    LEGACY_SCHEMA_NAME,
    METADATA_COLUMNS,
    METADATA_SCHEMA,
    MODERN_COLUMNS,
    STAGE_SCHEMA,
    clean_text,
    detect_encoding,
    detect_schema,
    field_size_limit,
    legacy_id,
    modern_id,
    normalize_date,
    normalize_degree,
    normalize_keywords,
    normalize_pages,
    normalize_url,
    normalize_year,
    setup_logging,
)

LOGGER = setup_logging("normalize")
STAGE_BATCH_ROWS = 20_000
FINAL_BATCH_ROWS = 50_000

LEGACY_ADVISOR_COLUMNS: tuple[str, ...] = tuple(
    [f"Orientador_{index}" for index in range(1, 5)]
    + [f"CoOrientador_{index}" for index in range(1, 5)]
)


def _get(values: Sequence[str], index: int | None) -> str | None:
    """Return a CSV cell by index, tolerating short rows."""
    if index is None or index >= len(values):
        return None
    return values[index]


def _header_index(header: Sequence[str]) -> dict[str, int]:
    """Map header names (BOM stripped, stripped) to positions."""
    return {name.lstrip("﻿").strip(): index for index, name in enumerate(header)}


def _legacy_advisors(values: Sequence[str], indexes: dict[str, int]) -> str | None:
    advisors = [
        cleaned
        for cleaned in (
            clean_text(_get(values, indexes.get(name))) for name in LEGACY_ADVISOR_COLUMNS[:4]
        )
        if cleaned
    ]
    co_advisors = [
        f"{cleaned} (coorientador)"
        for cleaned in (
            clean_text(_get(values, indexes.get(name))) for name in LEGACY_ADVISOR_COLUMNS[4:]
        )
        if cleaned
    ]
    joined = advisors + co_advisors
    return "; ".join(joined) or None


def _extract_legacy(values: Sequence[str], indexes: dict[str, int], source: str) -> list[Any] | None:
    """Build one output row from a legacy CSV row (``None`` if no title)."""
    title = clean_text(_get(values, indexes.get(LEGACY_COLUMNS["title"])))
    if title is None:
        return None
    year = normalize_year(_get(values, indexes.get(LEGACY_COLUMNS["year"])))
    program_code = clean_text(_get(values, indexes.get(LEGACY_COLUMNS["program_code"])))
    author = clean_text(_get(values, indexes.get(LEGACY_COLUMNS["author"])))
    return [
        legacy_id(year, program_code, author, title),
        year,
        normalize_date(_get(values, indexes.get(LEGACY_COLUMNS["defense_date"]))),
        title,
        author,
        _legacy_advisors(values, indexes),
        normalize_degree(_get(values, indexes.get(LEGACY_COLUMNS["degree"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["institution"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["institution_acronym"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["uf"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["region"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["program"]))),
        program_code,
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["great_area"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["knowledge_area"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["evaluation_area"]))),
        None,
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["research_line"]))),
        normalize_keywords(
            _get(values, indexes.get(LEGACY_COLUMNS["keywords"])), legacy=True
        ),
        None,
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["abstract"]))),
        None,
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["language"]))),
        normalize_pages(_get(values, indexes.get(LEGACY_COLUMNS["pages"]))),
        clean_text(_get(values, indexes.get(LEGACY_COLUMNS["library"]))),
        normalize_url(_get(values, indexes.get(LEGACY_COLUMNS["url"]))),
        source,
    ]


def _extract_modern(values: Sequence[str], indexes: dict[str, int], source: str) -> list[Any] | None:
    """Build one output row from a modern CSV row (``None`` if unusable)."""
    title = clean_text(_get(values, indexes.get(MODERN_COLUMNS["title"])))
    if title is None:
        return None
    identifier = modern_id(_get(values, indexes.get(MODERN_COLUMNS["id"])))
    if identifier is None:
        return None
    degree_value = _get(values, indexes.get(MODERN_COLUMNS["degree"]))
    if clean_text(degree_value) is None:
        degree_value = _get(values, indexes.get(MODERN_COLUMNS["degree_fallback"]))
    return [
        identifier,
        normalize_year(_get(values, indexes.get(MODERN_COLUMNS["year"]))),
        normalize_date(_get(values, indexes.get(MODERN_COLUMNS["defense_date"]))),
        title,
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["author"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["advisors"]))),
        normalize_degree(degree_value),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["institution"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["institution_acronym"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["uf"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["region"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["program"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["program_code"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["great_area"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["knowledge_area"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["evaluation_area"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["concentration_area"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["research_line"]))),
        normalize_keywords(_get(values, indexes.get(MODERN_COLUMNS["keywords"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["keywords_en"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["abstract"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["abstract_en"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["language"]))),
        normalize_pages(_get(values, indexes.get(MODERN_COLUMNS["pages"]))),
        clean_text(_get(values, indexes.get(MODERN_COLUMNS["library"]))),
        normalize_url(_get(values, indexes.get(MODERN_COLUMNS["url"]))),
        source,
    ]


class _StageWriter:
    """Buffered writer for one intermediate Parquet file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = pq.ParquetWriter(path, STAGE_SCHEMA, compression="zstd")
        self._buffers: list[list[Any]] = [[] for _ in METADATA_COLUMNS]
        self._rows: list[int] = []

    def append(self, record: list[Any], row_number: int) -> None:
        for buffer, value in zip(self._buffers, record, strict=True):
            buffer.append(value)
        self._rows.append(row_number)
        if len(self._rows) >= STAGE_BATCH_ROWS:
            self.flush()

    def flush(self) -> None:
        if not self._rows:
            return
        arrays = [
            pa.array(buffer, type=METADATA_SCHEMA.field(index).type)
            for index, buffer in enumerate(self._buffers)
        ]
        arrays.append(pa.array(self._rows, type=pa.int64()))
        table = pa.Table.from_arrays(arrays, schema=STAGE_SCHEMA)
        self._writer.write_table(table)
        self._buffers = [[] for _ in METADATA_COLUMNS]
        self._rows = []

    def close(self) -> None:
        self.flush()
        self._writer.close()


def convert_csv(csv_path: Path, stage_path: Path) -> dict[str, Any]:
    """Convert one raw CSV into a staged Parquet file."""
    encoding = detect_encoding(csv_path)
    field_size_limit()
    rows_read = 0
    discarded = 0
    staged = 0
    extracted: list[Any] | None
    with csv_path.open("r", encoding=encoding, errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter=";", quotechar='"')
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"CSV vazio: {csv_path.name}") from None
        schema_name = detect_schema(header)
        indexes = _header_index(header)
        writer = _StageWriter(stage_path)
        try:
            if schema_name == LEGACY_SCHEMA_NAME:
                for values in reader:
                    rows_read += 1
                    extracted = _extract_legacy(values, indexes, csv_path.name)
                    if extracted is None:
                        discarded += 1
                        continue
                    writer.append(extracted, rows_read)
                    staged += 1
            else:
                for values in reader:
                    rows_read += 1
                    extracted = _extract_modern(values, indexes, csv_path.name)
                    if extracted is None:
                        discarded += 1
                        continue
                    writer.append(extracted, rows_read)
                    staged += 1
        finally:
            writer.close()
    LOGGER.info(
        "%s: %d linha(s), %d descartada(s), %d estagiada(s) [%s]",
        csv_path.name,
        rows_read,
        discarded,
        staged,
        schema_name,
    )
    return {
        "schema": schema_name,
        "encoding": encoding,
        "rows_read": rows_read,
        "discarded_no_title_or_id": discarded,
        "rows_staged": staged,
    }


def _quoted_list(paths: Sequence[Path]) -> str:
    return json.dumps([str(path) for path in paths], ensure_ascii=False)


def _filled_expression() -> str:
    return " + ".join(
        f"(CASE WHEN {column} IS NOT NULL THEN 1 ELSE 0 END)" for column in METADATA_COLUMNS
    )


def _write_parts(
    connection: duckdb.DuckDBPyConnection,
    out_dir: Path,
    rows_per_part: int,
) -> list[dict[str, Any]]:
    """Stream the deduplicated rows into sorted Parquet parts."""
    final_sql = (
        "SELECT id, CAST(year AS SMALLINT) AS year, "
        + ", ".join(METADATA_COLUMNS[2:23])
        + ", CAST(pages AS INTEGER) AS pages, "
        + ", ".join(METADATA_COLUMNS[24:])
        + " FROM dedup ORDER BY year ASC, id ASC"
    )
    reader = connection.execute(final_sql).to_arrow_reader(FINAL_BATCH_ROWS)
    parts: list[dict[str, Any]] = []
    writer: pq.ParquetWriter | None = None
    part_index = 0
    part_rows = 0
    part_path: Path | None = None

    def open_part() -> tuple[pq.ParquetWriter, Path]:
        nonlocal part_index
        path = out_dir / f"part-{part_index:03d}.parquet"
        return pq.ParquetWriter(path, METADATA_SCHEMA, compression="zstd"), path

    def close_part() -> None:
        nonlocal writer, part_rows, part_path
        if writer is None or part_path is None:
            return
        writer.close()
        parts.append({"path": part_path.name, "rows": part_rows})
        writer = None
        part_path = None
        part_rows = 0

    try:
        for batch in reader:
            table = pa.Table.from_batches([batch.cast(METADATA_SCHEMA)], schema=METADATA_SCHEMA)
            offset = 0
            while offset < table.num_rows:
                if writer is None:
                    writer, part_path = open_part()
                take = min(rows_per_part - part_rows, table.num_rows - offset)
                chunk = table.slice(offset, take)
                writer.write_table(chunk, row_group_size=FINAL_BATCH_ROWS)
                offset += take
                part_rows += take
                if part_rows >= rows_per_part:
                    part_index += 1
                    close_part()
    finally:
        close_part()
    return parts


def normalize(
    raw_dir: Path,
    out_dir: Path,
    *,
    rows_per_part: int = 250_000,
    stage_dir: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Run the full normalisation and return the report dictionary."""
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    stage_dir = Path(stage_dir) if stage_dir else out_dir.parent / "stage"
    report_path = Path(report_path) if report_path else out_dir.parent / "normalize_report.json"
    if raw_dir.resolve() == out_dir.resolve():
        raise ValueError("--raw e --out devem ser diretórios diferentes")
    if rows_per_part <= 0:
        raise ValueError("--rows-per-part deve ser positivo")
    csv_files = sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"Nenhum CSV encontrado em {raw_dir}")

    stage_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("part-*.parquet"):
        stale.unlink()

    files_report: dict[str, Any] = {}
    for csv_path in csv_files:
        stage_path = stage_dir / f"{csv_path.stem}.parquet"
        LOGGER.info("Convertendo %s", csv_path.name)
        files_report[csv_path.name] = convert_csv(csv_path, stage_path)

    stage_files = sorted(stage_dir.glob("*.parquet"))
    database_path = stage_dir / "normalize.duckdb"
    database_path.unlink(missing_ok=True)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("SET memory_limit='4GB'")
        connection.execute("SET threads=2")
        connection.execute(f"SET temp_directory='{stage_dir / 'duckdb_tmp'}'")
        connection.execute(
            f"CREATE VIEW staged AS SELECT * FROM read_parquet({_quoted_list(stage_files)})"
        )
        total_staged = int(connection.execute("SELECT count(*) FROM staged").fetchone()[0])
        connection.execute(
            "CREATE VIEW dedup AS "
            f"SELECT {', '.join(METADATA_COLUMNS)} FROM ("
            "  SELECT *, ROW_NUMBER() OVER ("
            "    PARTITION BY id ORDER BY filled DESC, source DESC, __row ASC"
            "  ) AS rn FROM ("
            f"    SELECT *, {_filled_expression()} AS filled FROM staged"
            "  )"
            ") WHERE rn = 1"
        )
        total_final = int(connection.execute("SELECT count(*) FROM dedup").fetchone()[0])
        if total_final == 0:
            raise ValueError("Nenhum registro válido após a normalização")
        by_year_rows = connection.execute(
            "SELECT year, count(*) FROM dedup GROUP BY year ORDER BY year"
        ).fetchall()
        null_rows = connection.execute(
            "SELECT "
            + ", ".join(
                f"avg(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) * 100"
                for column in METADATA_COLUMNS
            )
            + " FROM dedup"
        ).fetchone()
        parts = _write_parts(connection, out_dir, rows_per_part)
    finally:
        connection.close()
        database_path.unlink(missing_ok=True)
        shutil.rmtree(stage_dir / "duckdb_tmp", ignore_errors=True)

    report = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "rows_per_part": rows_per_part,
        "files": files_report,
        "rows_read": sum(item["rows_read"] for item in files_report.values()),
        "discarded_no_title_or_id": sum(
            item["discarded_no_title_or_id"] for item in files_report.values()
        ),
        "rows_staged": total_staged,
        "duplicates_removed": total_staged - total_final,
        "total": total_final,
        "parts": parts,
        "by_year": {str(year): count for year, count in by_year_rows if year is not None},
        "null_percent": {
            column: round(float(percent or 0.0), 2)
            for column, percent in zip(METADATA_COLUMNS, null_rows, strict=True)
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info(
        "Concluído: %d registro(s), %d duplicata(s), %d parte(s); relatório em %s",
        total_final,
        report["duplicates_removed"],
        len(parts),
        report_path,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Normaliza os CSVs da CAPES em Parquet")
    parser.add_argument("--raw", type=Path, required=True, help="diretório com os CSVs originais")
    parser.add_argument("--out", type=Path, required=True, help="diretório metadata/ de saída")
    parser.add_argument("--rows-per-part", type=int, default=250_000)
    parser.add_argument("--stage", type=Path, default=None, help="diretório de Parquet intermediário")
    parser.add_argument("--report", type=Path, default=None, help="caminho do normalize_report.json")
    arguments = parser.parse_args(argv)
    normalize(
        arguments.raw,
        arguments.out,
        rows_per_part=arguments.rows_per_part,
        stage_dir=arguments.stage,
        report_path=arguments.report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
