"""Synthetic dataset fixtures shared by the backend tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from buscateses.catalog.installer import CatalogInstaller
from buscateses.catalog.search import CatalogSearch
from buscateses.config import Paths, Settings, get_paths, get_settings
from buscateses.embeddings.base import format_document
from buscateses.embeddings.local_onnx import download_model

TEST_PORT = 8101
THEMES = (
    "educação inclusiva",
    "inteligência artificial",
    "saúde pública",
    "engenharia de materiais",
    "história do brasil",
    "direito ambiental",
    "computação quântica",
    "psicologia escolar",
)
DEGREES = ("Mestrado", "Doutorado", "Mestrado Profissional")
INSTITUTIONS = (
    ("UFMG", "Universidade Federal de Minas Gerais", "MG", "SUDESTE"),
    ("USP", "Universidade de São Paulo", "SP", "SUDESTE"),
    ("UFRJ", "Universidade Federal do Rio de Janeiro", "RJ", "SUDESTE"),
    ("UNB", "Universidade de Brasília", "DF", "CENTRO-OESTE"),
)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class FakeEmbedder:
    """Deterministic hashed bag-of-words embedder for tests."""

    name = "fake-hashed"
    dims_full = 768

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    def ready(self) -> bool:
        return True

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._normalized_vector(text) for text in texts]) if texts else np.zeros(
            (0, self.dims_full), dtype=np.float32
        )

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> np.ndarray:
        texts = [format_document(title, text) for title, text in documents]
        return self.embed_queries(texts)

    def vector_256(self, text: str) -> np.ndarray:
        vector = self._raw(text, self.dims)
        norm = np.linalg.norm(vector)
        if norm:
            vector = vector / norm
        return vector.astype(np.float32)

    def _normalized_vector(self, text: str) -> np.ndarray:
        full = np.zeros(self.dims_full, dtype=np.float32)
        full[: self.dims] = self._raw(text, self.dims)
        norm = np.linalg.norm(full)
        if norm:
            full /= norm
        return full

    @staticmethod
    def _raw(text: str, dims: int) -> np.ndarray:
        vector = np.zeros(dims, dtype=np.float32)
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % dims
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vector[index] += sign
        return vector


def _ascii_upper(text: str) -> str:
    """Uppercase *text* without diacritics (legacy CAPES style)."""
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii").upper()


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(text).casefold())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return [token for token in _TOKEN_RE.findall(ascii_text) if len(token) > 2]


@dataclass(frozen=True)
class DatasetInfo:
    """Paths and records of a generated synthetic dataset."""

    root: Path
    version: str
    manifest: dict[str, Any]
    records: list[dict[str, Any]]
    embedder: FakeEmbedder


def build_dataset(
    root: Path,
    *,
    version: str = "2026.10.21",
    count: int = 60,
    start_year: int = 1990,
    label: str = "",
) -> DatasetInfo:
    """Create metadata/embeddings Parquet files and a valid manifest."""
    root.mkdir(parents=True, exist_ok=True)
    embedder = FakeEmbedder()
    records = [_record(index, start_year, label) for index in range(count)]
    metadata_dir = root / "metadata"
    embeddings_dir = root / "embeddings-256"
    metadata_dir.mkdir(exist_ok=True)
    embeddings_dir.mkdir(exist_ok=True)
    files: list[dict[str, Any]] = []
    for part, start in enumerate((0, count // 2)):
        end = count if part == 1 else count // 2
        chunk = records[start:end]
        metadata_path = metadata_dir / f"part-{part:03d}.parquet"
        _write_metadata(metadata_path, chunk)
        files.append(_manifest_file(metadata_path, root, "metadata", len(chunk)))
        embeddings_path = embeddings_dir / f"part-{part:03d}.parquet"
        _write_embeddings(embeddings_path, chunk, embedder)
        files.append(_manifest_file(embeddings_path, root, "embeddings-256", len(chunk)))
    years = [min(int(row["year"]) for row in records), max(int(row["year"]) for row in records)]
    probe = embedder.embed_queries(["task: search result | query: educação inclusiva"])[0]
    manifest = {
        "schema": 1,
        "version": version,
        "created_at": "2026-10-21T00:00:00+00:00",
        "revision": version,
        "records": count,
        "years": years,
        "source": {
            "name": "CAPES — Catálogo de Teses e Dissertações",
            "url": "https://dadosabertos.capes.gov.br",
            "license": "CC-BY",
        },
        "embedding": {
            "model": "google/embeddinggemma-300m",
            "onnx_repo": "onnx-community/embeddinggemma-300m-ONNX",
            "app_dims": 256,
            "full_dims": 768,
            "document_template": "title: {title} | text: {text}",
            "query_template": "task: search result | query: {query}",
            "probe": {
                "text": "task: search result | query: educação inclusiva",
                "vector_768_head": [float(value) for value in probe[:16]],
            },
        },
        "files": files,
        "min_app_version": "1.0.0",
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return DatasetInfo(
        root=root,
        version=version,
        manifest=manifest,
        records=records,
        embedder=embedder,
    )


def _record(index: int, start_year: int, label: str) -> dict[str, Any]:
    theme = THEMES[index % len(THEMES)]
    acronym, institution, uf, region = INSTITUTIONS[index % len(INSTITUTIONS)]
    year = start_year + (index % 35)
    suffix = f" {label}" if label else ""
    title = f"Estudo sobre {theme} — volume {index + 1}{suffix}"
    abstract = (
        f"Este trabalho investiga {theme} em profundidade. "
        f"Aborda políticas públicas, métodos participativos e resultados "
        f"educacionais para o caso {index + 1}.{suffix}"
    )
    return {
        "id": f"capes:{index + 1:04d}",
        "year": year,
        "defense_date": f"{year}-03-{(index % 27) + 1:02d}",
        "title": title,
        "author": f"Autor Sobrenome{index + 1}",
        "advisors": f"Orientador Nome{index + 1}; Coorientador Nome{index + 1} (coorientador)",
        "degree": DEGREES[index % len(DEGREES)],
        "institution": institution,
        "institution_acronym": acronym,
        "uf": uf,
        "region": region,
        "program": f"PROGRAMA DE POS-GRADUACAO EM {_ascii_upper(theme)}",
        "program_code": f"P{index % 7:02d}",
        "great_area": "CIENCIAS HUMANAS" if index % 2 else "CIENCIAS EXATAS",
        "knowledge_area": _ascii_upper(theme),
        "evaluation_area": "EDUCACAO" if index % 3 else "ENGENHARIAS",
        "concentration_area": f"Área {index % 5}",
        "research_line": f"Linha de pesquisa {index % 4}",
        "keywords": f"{theme}; pesquisa; brasil",
        "keywords_en": f"{theme}; research; brazil",
        "abstract": abstract,
        "abstract_en": f"This work studies {theme}.{suffix}",
        "language": "Português",
        "pages": 80 + index,
        "library": "Biblioteca Central",
        # One record intentionally has no full-text URL (Scholar fallback).
        "url": None if index == 1 else f"https://example.org/tese/{index + 1}",
        "source": "sintetico.csv",
    }


def _write_metadata(path: Path, records: Sequence[dict[str, Any]]) -> None:
    columns: dict[str, list[Any]] = {key: [] for key in records[0]}
    for record in records:
        for key, value in record.items():
            columns[key].append(value)
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("year", pa.int16()),
            ("defense_date", pa.string()),
            ("title", pa.string()),
            ("author", pa.string()),
            ("advisors", pa.string()),
            ("degree", pa.string()),
            ("institution", pa.string()),
            ("institution_acronym", pa.string()),
            ("uf", pa.string()),
            ("region", pa.string()),
            ("program", pa.string()),
            ("program_code", pa.string()),
            ("great_area", pa.string()),
            ("knowledge_area", pa.string()),
            ("evaluation_area", pa.string()),
            ("concentration_area", pa.string()),
            ("research_line", pa.string()),
            ("keywords", pa.string()),
            ("keywords_en", pa.string()),
            ("abstract", pa.string()),
            ("abstract_en", pa.string()),
            ("language", pa.string()),
            ("pages", pa.int32()),
            ("library", pa.string()),
            ("url", pa.string()),
            ("source", pa.string()),
        ]
    )
    table = pa.Table.from_pydict(columns, schema=schema)
    pq.write_table(table, path, compression="zstd")


def _write_embeddings(
    path: Path,
    records: Sequence[dict[str, Any]],
    embedder: FakeEmbedder,
) -> None:
    identifiers = [row["id"] for row in records]
    vectors = [
        embedder.vector_256(f"{row['title']} {row['abstract']}").tolist() for row in records
    ]
    embedding_type = pa.list_(pa.float16(), 256)
    table = pa.Table.from_arrays(
        [
            pa.array(identifiers, type=pa.string()),
            pa.array(vectors, type=embedding_type),
        ],
        names=["id", "embedding"],
    )
    pq.write_table(table, path, compression="zstd")


def _manifest_file(path: Path, root: Path, kind: str, rows: int) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "rows": rows,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


@pytest.fixture
def dataset_factory(tmp_path: Path):
    """Return a factory that writes synthetic dataset releases."""

    def factory(
        name: str = "dataset",
        *,
        version: str = "2026.10.21",
        count: int = 60,
        label: str = "",
    ) -> DatasetInfo:
        return build_dataset(
            tmp_path / name,
            version=version,
            count=count,
            label=label,
        )

    return factory


@pytest.fixture
def dataset(dataset_factory) -> DatasetInfo:
    return dataset_factory()


@pytest.fixture
def app_env(dataset: DatasetInfo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Configure env vars and return paths/settings for a fresh home."""
    monkeypatch.setenv("BUSCATESES_DATASET_URL", str(dataset.root))
    monkeypatch.setenv("BUSCATESES_HOME", str(tmp_path / "home"))
    paths = get_paths()
    settings = get_settings(paths)
    return {"dataset": dataset, "paths": paths, "settings": settings}


@pytest.fixture
def installed(app_env) -> dict[str, Any]:
    """Install the synthetic catalog and return useful handles."""
    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    installer.install()
    release = installer.current_release_dir()
    assert release is not None
    return {
        **app_env,
        "installer": installer,
        "release": release,
        "catalog": CatalogSearch(release, app_env["dataset"].embedder),
    }


@pytest.fixture
def client(app_env):
    """TestClient for an app with no installed catalog."""

    from buscateses.api import create_app

    app = create_app(
        app_env["paths"],
        app_env["settings"],
        embedder=app_env["dataset"].embedder,
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as test_client:
        yield test_client


def auth_headers() -> dict[str, str]:
    """Headers required for local mutating requests."""
    return {"X-BuscaTeses": "1"}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip model-download tests unless explicitly requested."""
    del config
    if os.getenv("BUSCATESES_RUN_SLOW") == "1":
        return
    skip = pytest.mark.skip(
        reason="teste lento desativado; defina BUSCATESES_RUN_SLOW=1 para executar"
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def real_model_available() -> bool:
    """Whether the slow real-model test was requested."""
    return os.getenv("BUSCATESES_RUN_SLOW") == "1"


__all__ = [
    "DatasetInfo",
    "FakeEmbedder",
    "Paths",
    "Settings",
    "TEST_PORT",
    "auth_headers",
    "build_dataset",
    "download_model",
]
