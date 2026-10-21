"""Optional performance benchmark for the semantic scan (SPEC §4)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from buscateses.catalog.builder import CATALOG_DB, VECTORS_FILE
from buscateses.catalog.search import CatalogSearch

COUNT = 1_000_000
BLOCK = 50_000
MAX_SECONDS = 0.6


def _write_vectors(path: Path) -> None:
    vectors = np.lib.format.open_memmap(
        path, mode="w+", dtype=np.float16, shape=(COUNT, 256)
    )
    rng = np.random.default_rng(1234)
    try:
        for start in range(0, COUNT, BLOCK):
            end = min(start + BLOCK, COUNT)
            block = rng.standard_normal((end - start, 256), dtype=np.float32)
            norms = np.linalg.norm(block, axis=1, keepdims=True)
            vectors[start:end] = (block / norms).astype(np.float16)
        vectors.flush()
    finally:
        del vectors


def _write_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            [
                ("records", str(COUNT)),
                ("format_version", "2"),
                ("vector_dtype", "float16"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.slow
def test_semantic_scan_one_million_rows(tmp_path: Path) -> None:
    """The float16 block scan must stay below 0.6 s on ordinary hardware."""
    _write_vectors(tmp_path / VECTORS_FILE)
    _write_database(tmp_path / CATALOG_DB)
    catalog = CatalogSearch(tmp_path)

    rng = np.random.default_rng(7)
    query = rng.standard_normal(256, dtype=np.float32)
    query /= np.linalg.norm(query)
    mask = np.ones(COUNT, dtype=bool)

    started = time.perf_counter()
    ranked = catalog._semantic_candidates(query, mask, 400)
    elapsed = time.perf_counter() - started

    assert len(ranked) == 400
    assert elapsed < MAX_SECONDS, f"busca semântica levou {elapsed:.3f}s"
