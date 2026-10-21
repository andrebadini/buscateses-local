"""Tests for release assembly with synthetic metadata and embeddings."""

from __future__ import annotations

import json
from pathlib import Path

import common
import finalize
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _metadata_row(identifier: str, year: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": identifier,
        "year": year,
        "defense_date": f"{year}-01-01",
        "title": f"Título {identifier}",
        "author": f"Autor {identifier}",
        "advisors": None,
        "degree": "Mestrado",
        "institution": "Universidade Teste",
        "institution_acronym": "UT",
        "uf": "DF",
        "region": "CENTRO-OESTE",
        "program": "Programa",
        "program_code": "P1",
        "great_area": "CIÊNCIAS HUMANAS",
        "knowledge_area": "EDUCAÇÃO",
        "evaluation_area": "EDUCAÇÃO",
        "concentration_area": None,
        "research_line": None,
        "keywords": "a; b",
        "keywords_en": None,
        "abstract": f"Resumo {identifier}",
        "abstract_en": None,
        "language": "Português",
        "pages": 100,
        "library": None,
        "url": None,
        "source": "sintetico.csv",
    }
    row.update(overrides)
    return row


def _write_metadata_part(path: Path, rows: list[dict[str, object]]) -> None:
    arrays = [
        pa.array([row[name] for row in rows], type=dtype)
        for name, dtype in common.METADATA_FIELDS
    ]
    pq.write_table(pa.Table.from_arrays(arrays, schema=common.METADATA_SCHEMA), path)


def _write_unit(path: Path, identifiers: list[str], matrix: np.ndarray) -> None:
    block = np.ascontiguousarray(matrix, dtype=np.float16)
    embedding = pa.FixedSizeListArray.from_arrays(
        pa.array(block.reshape(-1), type=pa.float16()), common.FULL_DIMS
    )
    table = pa.Table.from_arrays(
        [pa.array(identifiers, type=pa.string()), embedding], schema=common.EMBEDDING_SCHEMA
    )
    pq.write_table(table, path)


def _vectors() -> dict[str, np.ndarray]:
    vectors: dict[str, np.ndarray] = {}
    first = np.zeros(common.FULL_DIMS, dtype=np.float32)
    first[:256] = 3.0
    vectors["a"] = first
    vectors["b"] = np.linspace(0.1, 7.0, common.FULL_DIMS, dtype=np.float32)
    vectors["c"] = np.zeros(common.FULL_DIMS, dtype=np.float32)
    vectors["d"] = np.arange(1, common.FULL_DIMS + 1, dtype=np.float32)
    vectors["e"] = np.full(common.FULL_DIMS, 0.25, dtype=np.float32)
    vectors["f"] = np.full(common.FULL_DIMS, -2.0, dtype=np.float32)
    return vectors


def _build_inputs(tmp_path: Path, *, include_f: bool = True) -> tuple[Path, Path]:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    rows = {
        "a": _metadata_row("a", 2001),
        "b": _metadata_row("b", 2002),
        "c": _metadata_row("c", 2003),
        "d": _metadata_row("d", 2010),
        "e": _metadata_row("e", 2011),
        "f": _metadata_row("f", 2012),
    }
    _write_metadata_part(metadata / "part-000.parquet", [rows["a"], rows["b"], rows["c"]])
    _write_metadata_part(metadata / "part-001.parquet", [rows["d"], rows["e"], rows["f"]])

    embeddings = tmp_path / "emb"
    embeddings.mkdir()
    vectors = _vectors()
    # Deliberately out of order to prove alignment is by id.
    _write_unit(
        embeddings / "unit-00000.parquet",
        ["c", "a"],
        np.stack([vectors["c"], vectors["a"]]),
    )
    unit_ids = ["b", "d", "e"] + (["f"] if include_f else [])
    _write_unit(
        embeddings / "unit-00001.parquet",
        unit_ids,
        np.stack([vectors[identifier] for identifier in unit_ids]),
    )
    return metadata, embeddings


def test_finalize_aligns_and_builds_manifest(tmp_path: Path) -> None:
    metadata, embeddings = _build_inputs(tmp_path)
    out = tmp_path / "release"
    probe = [0.5] * common.FULL_DIMS

    manifest = finalize.finalize_release(
        metadata,
        embeddings,
        out,
        "2026.10.21",
        probe_vector=probe,
    )

    metadata_parts = sorted((out / "metadata").glob("part-*.parquet"))
    emb768_parts = sorted((out / "embeddings-768").glob("part-*.parquet"))
    emb256_parts = sorted((out / "embeddings-256").glob("part-*.parquet"))
    assert len(metadata_parts) == len(emb768_parts) == len(emb256_parts) == 2

    assert pq.read_table(metadata_parts[0]).to_pylist() == pq.read_table(
        metadata / "part-000.parquet"
    ).to_pylist()
    assert pq.read_table(metadata_parts[1]).to_pylist() == pq.read_table(
        metadata / "part-001.parquet"
    ).to_pylist()

    table768 = pa.concat_tables([pq.read_table(path) for path in emb768_parts])
    assert table768.column("id").to_pylist() == ["a", "b", "c", "d", "e", "f"]
    vectors = {
        identifier: np.asarray(vector, dtype=np.float32)
        for identifier, vector in zip(
            table768.column("id").to_pylist(),
            table768.column("embedding").to_pylist(),
            strict=True,
        )
    }
    assert np.allclose(vectors["a"][:256], 3.0, atol=1e-2)
    assert np.allclose(vectors["a"][256:], 0.0)
    assert np.allclose(vectors["b"], np.linspace(0.1, 7.0, common.FULL_DIMS), atol=1e-2)

    table256 = pa.concat_tables([pq.read_table(path) for path in emb256_parts])
    assert table256.column("id").to_pylist() == ["a", "b", "c", "d", "e", "f"]
    reduced = {
        identifier: np.asarray(vector, dtype=np.float32)
        for identifier, vector in zip(
            table256.column("id").to_pylist(),
            table256.column("embedding").to_pylist(),
            strict=True,
        )
    }
    # 'a' has 256 equal non-zero values; re-normalisation gives 1/sqrt(256).
    assert np.allclose(reduced["a"], 1.0 / 16.0, atol=1e-2)
    expected_b = np.linspace(0.1, 7.0, common.FULL_DIMS)[:256]
    expected_b = expected_b / np.linalg.norm(expected_b)
    assert np.allclose(reduced["b"], expected_b, atol=1e-2)
    assert np.allclose(reduced["c"], 0.0)
    assert np.allclose(np.linalg.norm(reduced["d"]), 1.0, atol=1e-2)

    assert manifest["schema"] == 1
    assert manifest["version"] == "2026.10.21"
    assert manifest["revision"] == "2026.10.21"
    assert manifest["records"] == 6
    assert manifest["years"] == [2001, 2012]
    assert manifest["min_app_version"] == "1.0.0"
    assert manifest["source"]["license"] == "CC-BY"
    assert manifest["embedding"]["app_dims"] == 256
    assert manifest["embedding"]["full_dims"] == 768
    assert manifest["embedding"]["probe"]["vector_768_head"] == [0.5] * 16
    assert manifest["embedding"]["probe"]["text"] == common.PROBE_TEXT
    assert "missing_embeddings" not in manifest

    assert len(manifest["files"]) == 6
    kinds = [entry["kind"] for entry in manifest["files"]]
    assert kinds.count("metadata") == 2
    assert kinds.count("embeddings-256") == 2
    assert kinds.count("embeddings-768") == 2
    for entry in manifest["files"]:
        path = out / entry["path"]
        assert path.is_file()
        assert path.stat().st_size == entry["bytes"]
        assert common.sha256_file(path) == entry["sha256"]
        assert entry["rows"] == 3

    stored = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert stored == manifest

    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "license: cc-by-4.0" in readme
    assert "Contagem por década" in readme
    assert "não afiliação" in readme.lower() or "não afiliação" in readme
    assert "https://dadosabertos.capes.gov.br" in readme
    assert "matryoshka" in readme.lower()


def test_dataset_card_documents_truecasing_and_citation() -> None:
    from collections import Counter

    card = finalize._render_dataset_card(
        "2026.10.21",
        1_670_528,
        [1987, 2024],
        Counter({1980: 10, 2020: 5}),
    )

    # The English summary uses English thousands separators.
    assert "1,670,528 records" in card
    assert "1.670.528" not in card
    assert "truecased" in card
    assert "lexicon derived from the catalogue itself" in card

    # Generation notes mention the truecasing step, model revision and limit.
    assert "Normalização de caixa (truecasing)" in card
    assert "antes do embedding" in card
    assert "google/embeddinggemma-300m" in card
    assert common.EMBEDDING_REVISION in card
    assert "512" in card and "tokens" in card
    assert "Os metadados publicados preservam o" in card
    assert "texto original da CAPES" in card

    # Citation: hyphen-free key, version year, author and CAPES attribution.
    assert "buscateses_capes_2026_10_21" in card
    assert "author       = {Badini, André}" in card
    assert "year         = {2026}" in card
    assert "note         = {Dados originais: CAPES" in card
    assert "Citação recomendada da fonte (CAPES)" in card
    assert "capes_catalogo_teses_dissertacoes" in card

    # New app section and corrected limitation statement.
    assert "Uso no aplicativo BuscaTeses" in card
    assert "embeddings-256" in card
    assert "2013 em diante estão integralmente em maiúsculas" in card
    assert "1987–1999" in card
    assert "preservam o texto original da CAPES" in card


def test_finalize_rejects_missing_embeddings(tmp_path: Path) -> None:
    metadata, embeddings = _build_inputs(tmp_path, include_f=False)
    out = tmp_path / "release"
    with pytest.raises(finalize.MissingEmbeddingsError):
        finalize.finalize_release(
            metadata, embeddings, out, "2026.10.21", probe_vector=[0.0] * common.FULL_DIMS
        )
    assert not (out / "manifest.json").exists()


def test_finalize_allow_missing_writes_zero_vectors(tmp_path: Path) -> None:
    metadata, embeddings = _build_inputs(tmp_path, include_f=False)
    out = tmp_path / "release"
    manifest = finalize.finalize_release(
        metadata,
        embeddings,
        out,
        "2026.10.21",
        allow_missing=True,
        probe_vector=[0.0] * common.FULL_DIMS,
    )
    assert manifest["missing_embeddings"] == 1

    table768 = pa.concat_tables(
        [pq.read_table(path) for path in sorted((out / "embeddings-768").glob("*.parquet"))]
    )
    rows = dict(
        zip(
            table768.column("id").to_pylist(),
            table768.column("embedding").to_pylist(),
            strict=True,
        )
    )
    assert np.allclose(np.asarray(rows["f"], dtype=np.float32), 0.0)
    restored = np.asarray(rows["a"], dtype=np.float32)
    assert np.allclose(restored[:256], 3.0, atol=1e-2)
    assert np.allclose(restored[256:], 0.0)


def test_finalize_combines_directories_and_records_sources(tmp_path: Path) -> None:
    historical = tmp_path / "metadata"
    recent = tmp_path / "recent-metadata"
    historical.mkdir()
    recent.mkdir()
    old_a = _metadata_row("a", 2001, source="dados_2001.csv")
    old_b = _metadata_row("b", 2002, source="dados_2002.csv")
    new_c = _metadata_row(
        "capes-catalogo:9", 2024, source="capes-catalogo", title="Recente C"
    )
    new_d = _metadata_row("bdtd:7", 2025, source="bdtd", title="Recente D")
    _write_metadata_part(historical / "part-000.parquet", [old_a, old_b])
    # Recent part deliberately out of order to prove the global sort.
    _write_metadata_part(recent / "part-007.parquet", [new_d, new_c])

    embeddings_old = tmp_path / "emb-old"
    embeddings_new = tmp_path / "emb-new"
    embeddings_old.mkdir()
    embeddings_new.mkdir()
    vectors = _vectors()
    _write_unit(embeddings_old / "unit-00000.parquet", ["a", "b"], np.stack([vectors["a"], vectors["b"]]))
    _write_unit(
        embeddings_new / "unit-00000.parquet",
        ["capes-catalogo:9", "bdtd:7"],
        np.stack([vectors["c"], vectors["d"]]),
    )

    out = tmp_path / "release"
    manifest = finalize.finalize_release(
        [historical, recent],
        [embeddings_old, embeddings_new],
        out,
        "2026.10.21",
        probe_vector=[0.0] * common.FULL_DIMS,
    )

    metadata_parts = sorted((out / "metadata").glob("part-*.parquet"))
    emb_parts = sorted((out / "embeddings-768").glob("part-*.parquet"))
    assert len(metadata_parts) == len(emb_parts) == 1
    table = pq.read_table(metadata_parts[0])
    assert table.column("id").to_pylist() == ["a", "b", "capes-catalogo:9", "bdtd:7"]
    assert table.column("year").to_pylist() == [2001, 2002, 2024, 2025]
    assert pq.read_table(emb_parts[0]).column("id").to_pylist() == [
        "a",
        "b",
        "capes-catalogo:9",
        "bdtd:7",
    ]

    sources = {item["name"]: item for item in manifest["sources"]}
    assert sources[common.SOURCE_NAME]["records"] == 2
    assert sources[finalize.CAPES_CATALOGUE_NAME]["records"] == 1
    assert sources[finalize.BDTD_NAME]["records"] == 1
    for item in manifest["sources"]:
        assert set(item) == {"name", "url", "records", "license_note"}
    assert manifest["source"]["license"] == "CC-BY"

    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "BDTD (IBICT)" in readme
    assert "cobertura de 2024 em diante" in readme
    assert "Citação recomendada da fonte (BDTD)" in readme
