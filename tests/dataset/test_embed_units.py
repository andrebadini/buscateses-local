"""Tests for unit claiming and writing in tools/dataset/embed.py."""

from __future__ import annotations

import os
import time
from pathlib import Path

import common
import embed
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from tokenizers import Tokenizer, models, pre_tokenizers, processors


class FakeBackend:
    """Deterministic backend that records how many texts it embedded."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        matrix = np.zeros((len(texts), common.FULL_DIMS), dtype=np.float32)
        for row, text in enumerate(texts):
            matrix[row, 0] = float(len(text))
            matrix[row, 1] = float(row)
        matrix[:, 0:2] += 0.5
        return matrix


def _tokenizer() -> Tokenizer:
    vocabulary = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "title:": 3, "|": 4, "text:": 5, "none": 6}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        special_tokens=[("[CLS]", 1), ("[SEP]", 2)],
    )
    return tokenizer


def _write_metadata(directory: Path, rows: int) -> None:
    directory.mkdir()
    identifiers = [f"id-{index:05d}" for index in range(rows)]
    arrays = [
        pa.array(identifiers, type=pa.string()),
        pa.array([2000 + (index % 20) for index in range(rows)], type=pa.int16()),
    ]
    arrays.extend(pa.array([None] * rows, type=dtype) for _, dtype in common.METADATA_FIELDS[2:])
    table = pa.Table.from_arrays(arrays, schema=common.METADATA_SCHEMA)
    pq.write_table(table, directory / "part-000.parquet")


def test_embed_writes_unit_and_skips_completed(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    out = tmp_path / "emb"
    _write_metadata(metadata, 100)
    backend = FakeBackend()

    processed = embed.embed_metadata(metadata, out, backend, _tokenizer(), max_units=1)
    assert processed == 1
    assert backend.calls == 1
    unit = out / "unit-00000.parquet"
    assert unit.is_file()
    assert not list(out.glob("*.tmp"))
    assert not list(out.glob("*.lock"))

    table = pq.read_table(unit)
    assert table.schema.names == ["id", "embedding"]
    assert table.schema.field("embedding").type == pa.list_(pa.float16(), common.FULL_DIMS)
    assert table.num_rows == 100
    assert table.column("id").to_pylist() == [f"id-{index:05d}" for index in range(100)]

    # Running again must not call the backend.
    processed = embed.embed_metadata(metadata, out, backend, _tokenizer(), max_units=1)
    assert processed == 0
    assert backend.calls == 1


def test_embed_skips_a_unit_locked_by_another_process(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    out = tmp_path / "emb"
    _write_metadata(metadata, embed.UNIT_ROWS + 10)
    out.mkdir()
    lock = out / "unit-00000.lock"
    lock.write_text("{\"pid\": 1}", encoding="utf-8")
    backend = FakeBackend()

    processed = embed.embed_metadata(metadata, out, backend, _tokenizer(), max_units=1)
    assert processed == 1
    assert (out / "unit-00001.parquet").is_file()
    assert not (out / "unit-00000.parquet").exists()
    assert lock.is_file()


def test_claim_unit_recovers_abandoned_lock(tmp_path: Path) -> None:
    out = tmp_path / "emb"
    out.mkdir()
    lock = out / "unit-00000.lock"
    lock.write_text("{\"pid\": 1}", encoding="utf-8")
    old = time.time() - embed.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))

    assert embed.claim_unit(out, 0)
    assert lock.is_file()
    # A fresh lock cannot be claimed again.
    assert not embed.claim_unit(out, 0)


def test_embed_reads_a_numbered_recent_part(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    identifiers = ["bdtd:1", "capes-catalogo:2", "bdtd:3"]
    arrays = [
        pa.array(identifiers, type=pa.string()),
        pa.array([2025] * len(identifiers), type=pa.int16()),
    ]
    arrays.extend(
        pa.array([None] * len(identifiers), type=dtype)
        for _, dtype in common.METADATA_FIELDS[2:]
    )
    table = pa.Table.from_arrays(arrays, schema=common.METADATA_SCHEMA)
    pq.write_table(table, metadata / "part-007.parquet")

    out = tmp_path / "emb"
    backend = FakeBackend()
    processed = embed.embed_metadata(metadata, out, backend, _tokenizer(), max_units=1)

    assert processed == 1
    assert backend.calls == 1
    written = pq.read_table(out / "unit-00000.parquet")
    assert written.column("id").to_pylist() == identifiers
