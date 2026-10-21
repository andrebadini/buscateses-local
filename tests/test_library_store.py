"""Tests for library storage, ingestion, vectors and hybrid search."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from filelock import FileLock
from fpdf import FPDF

from buscateses.library.ingest import LibraryIngestor, reindex_missing
from buscateses.library.store import (
    MAX_FILE_BYTES,
    LibraryError,
    LibraryStore,
    media_type_for,
    safe_filename,
    sha256_file,
)
from tests.conftest import FakeEmbedder

TEXT = (
    "A educação inclusiva é o tema central desta pesquisa. "
    "O trabalho analisa políticas públicas e práticas escolares. "
) * 12


def _write_text(tmp_path: Path, name: str = "nota.txt", content: str = TEXT) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _ingestor(paths, embedder=None) -> tuple[LibraryStore, LibraryIngestor]:
    store = LibraryStore(paths)
    return store, LibraryIngestor(paths, embedder, store=store)


def test_ingest_moves_the_source_instead_of_copying(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths)
    source = _write_text(paths.home, "entrada.txt")

    outcome = ingestor.ingest_path(source, move_source=True)

    assert outcome.status == "ready"
    assert not source.exists()
    assert store.document_file(outcome.doc_id).is_file()


def test_ingest_falls_back_to_copy_across_filesystems(app_env, monkeypatch) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths)
    source = _write_text(paths.home, "entrada.txt")
    monkeypatch.setattr(ingestor, "_move", lambda *args, **kwargs: False)

    outcome = ingestor.ingest_path(source, move_source=True)

    assert outcome.status == "ready"
    assert source.exists()
    assert store.document_file(outcome.doc_id).is_file()


def test_library_writes_respect_the_cross_process_lock(app_env) -> None:
    paths = app_env["paths"]
    store = LibraryStore(paths, lock_timeout=0.05)
    lock = FileLock(str(paths.library_lock))
    lock.acquire()
    try:
        with pytest.raises(LibraryError, match="sendo usada"):
            store.create_document(
                doc_id="x",
                sha256="a" * 64,
                filename="x.txt",
                kind="txt",
                added_at="2026-01-01T00:00:00+00:00",
            )
    finally:
        lock.release()

    store.create_document(
        doc_id="y",
        sha256="b" * 64,
        filename="y.txt",
        kind="txt",
        added_at="2026-01-01T00:00:00+00:00",
    )
    assert store.get_document("y") is not None


def test_library_relaxation_drops_the_broad_term(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths)
    ingestor.ingest_path(
        _write_text(
            paths.home,
            "inclusiva.txt",
            content="A educação inclusiva orienta políticas escolares.",
        )
    )
    ingestor.ingest_path(
        _write_text(paths.home, "saude.txt", content="saúde pública " * 300)
    )

    result = store.search("inclusiva educação saúde")

    # Dropping "saúde" keeps the selective pair instead of matching both files.
    assert result["relaxed"] is True
    assert result["total"] == 1
    assert result["results"][0]["filename"] == "inclusiva.txt"


def test_ingest_embeds_and_supports_hybrid_search(app_env) -> None:
    paths = app_env["paths"]
    embedder = FakeEmbedder()
    store, ingestor = _ingestor(paths, embedder)
    outcome = ingestor.ingest_path(_write_text(Path(paths.home)))
    assert outcome.skipped is False
    assert outcome.status == "ready"
    assert outcome.warnings == []

    document = store.get_document(outcome.doc_id)
    assert document is not None
    assert document["embedded"] is True
    assert document["status"] == "ready"
    assert document["chunks"] > 0
    assert document["file_size"] > 0
    assert store.chunk_count(outcome.doc_id) == document["chunks"]
    assert store.counts() == {
        "documents": 1,
        "chunks": document["chunks"],
        "pending": 0,
    }
    assert paths.library_vectors.is_file()
    matrix = np.load(paths.library_vectors)
    assert matrix.shape == (document["chunks"], 768)
    assert np.load(paths.library_vector_ids).shape == (document["chunks"],)

    result = store.search("educação inclusiva", embedder=embedder)
    assert result["mode_used"] == "hybrid"
    assert result["total"] >= 1
    first = result["results"][0]
    assert first["doc_id"] == outcome.doc_id
    assert "texto" in first["matched_by"]
    assert "significado" in first["matched_by"]
    assert first["page_start"] == 1

    without_accent = store.search("educacao", embedder=embedder)
    assert without_accent["results"]
    assert "texto" in without_accent["results"][0]["matched_by"]

    passage = store.get_passage(first["chunk_id"], context_chunks=1)
    assert passage["doc_id"] == outcome.doc_id
    assert passage["text"]
    assert any(item["is_target"] for item in passage["chunks"])


def test_duplicate_is_skipped_and_document_preserved(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths, FakeEmbedder())
    source = _write_text(Path(paths.home))
    first = ingestor.ingest_path(source)
    second = ingestor.ingest_path(source, filename="copia.txt")
    assert second.skipped is True
    assert second.doc_id == first.doc_id
    assert "já está na biblioteca" in second.message
    assert store.counts()["documents"] == 1


def test_ingest_without_embedder_is_text_only_and_reindex_fills_vectors(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths, None)
    outcome = ingestor.ingest_path(_write_text(Path(paths.home)))
    assert outcome.status == "ready"
    assert "semantic_unavailable" in outcome.warnings
    document = store.get_document(outcome.doc_id)
    assert document is not None
    assert document["embedded"] is False

    text_only = store.search("educação inclusiva")
    assert text_only["mode_used"] == "text"
    assert "semantic_unavailable" in text_only["warnings"]
    assert text_only["results"]

    with pytest.raises(LibraryError, match="modelo"):
        reindex_missing(store, None)
    embedder = FakeEmbedder()
    count = reindex_missing(store, embedder)
    assert count == document["chunks"]
    assert reindex_missing(store, embedder) == 0
    updated = store.get_document(outcome.doc_id)
    assert updated is not None and updated["embedded"] is True
    assert store.search("educação inclusiva", embedder=embedder)["mode_used"] == "hybrid"


def test_needs_ocr_pdf_has_no_chunks(app_env, tmp_path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    path = tmp_path / "digitalizado.pdf"
    pdf.output(str(path))
    store, ingestor = _ingestor(app_env["paths"], FakeEmbedder())
    outcome = ingestor.ingest_path(path)
    assert outcome.status == "needs_ocr"
    assert "OCR" in outcome.message
    document = store.get_document(outcome.doc_id)
    assert document is not None
    assert document["status"] == "needs_ocr"
    assert document["title"] == "Digitalizado"
    assert document["chunks"] == 0
    assert store.counts()["pending"] == 0


def test_ingest_failures_are_friendly(app_env, tmp_path: Path) -> None:
    paths = app_env["paths"]
    _, ingestor = _ingestor(paths, FakeEmbedder())
    with pytest.raises(LibraryError, match="não foi encontrado"):
        ingestor.ingest_path(tmp_path / "ausente.txt")
    unsupported = tmp_path / "arquivo.bin"
    unsupported.write_bytes(b"x")
    with pytest.raises(LibraryError, match="Formato não suportado"):
        ingestor.ingest_path(unsupported)
    empty = tmp_path / "vazio.txt"
    empty.write_text("   ", encoding="utf-8")
    with pytest.raises(LibraryError, match="não contém texto"):
        ingestor.ingest_path(empty)
    assert LibraryStore(paths).counts()["documents"] == 1


def test_delete_removes_file_chunks_and_vectors(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths, FakeEmbedder())
    outcome = ingestor.ingest_path(_write_text(Path(paths.home)))
    document = store.get_document(outcome.doc_id)
    assert document is not None
    stored_file = store.file_path_for(document["sha256"], document["kind"])
    assert stored_file.is_file()
    assert store.delete_document(outcome.doc_id) is True
    assert store.delete_document(outcome.doc_id) is False
    assert not stored_file.exists()
    assert store.counts() == {"documents": 0, "chunks": 0, "pending": 0}
    assert np.load(paths.library_vectors).shape[0] == 0


def test_search_filters_pagination_and_validation(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths, FakeEmbedder())
    first = ingestor.ingest_path(_write_text(Path(paths.home), "a.txt", TEXT))
    second = ingestor.ingest_path(
        _write_text(Path(paths.home), "b.txt", TEXT + " Saúde pública também.")
    )
    result = store.search("educação", embedder=FakeEmbedder(), doc_id=second.doc_id)
    assert result["results"]
    assert {item["doc_id"] for item in result["results"]} == {second.doc_id}
    assert first.doc_id != second.doc_id

    paged = store.search("educação", page=1, page_size=1)
    assert paged["page_size"] == 1
    assert len(paged["results"]) <= 1
    assert paged["pages"] >= 1

    with pytest.raises(LibraryError, match="Digite"):
        store.search("   ")
    with pytest.raises(LibraryError, match="página"):
        store.search("x", page=0)
    with pytest.raises(LibraryError, match="tamanho"):
        store.search("x", page_size=100)
    with pytest.raises(LibraryError, match="não encontrado"):
        store.search("x", doc_id="nao-existe")
    with pytest.raises(LibraryError, match="não encontrado"):
        store.get_passage(999999)
    with pytest.raises(LibraryError, match="contexto"):
        store.get_passage(1, context_chunks=9)


def test_helpers_and_read_only_store(app_env, tmp_path: Path) -> None:
    assert safe_filename("../pasta\\arquivo\x00.txt") == "arquivo.txt"
    assert safe_filename("") == "documento"
    assert safe_filename("a" * 300).endswith("a")
    assert len(safe_filename("a" * 300)) <= 180
    assert media_type_for("pdf") == "application/pdf"
    assert media_type_for("txt").startswith("text/plain")
    assert media_type_for("exe") == "application/octet-stream"
    path = tmp_path / "hash.txt"
    path.write_text("abc", encoding="utf-8")
    assert len(sha256_file(path)) == 64

    paths = app_env["paths"]
    store, ingestor = _ingestor(paths, FakeEmbedder())
    outcome = ingestor.ingest_path(_write_text(tmp_path, "fonte.txt"))
    read_only = LibraryStore(paths, read_only=True)
    assert read_only.list_documents()[0]["id"] == outcome.doc_id
    assert read_only.search("educação")["total"] >= 1

    empty_read_only = LibraryStore(tmp_path / "nada", read_only=True)
    with pytest.raises(LibraryError, match="biblioteca ainda está vazia"):
        empty_read_only.list_documents()


def test_max_file_bytes_constant() -> None:
    assert MAX_FILE_BYTES == 500 * 1024 * 1024


def test_text_search_falls_back_to_or(app_env) -> None:
    paths = app_env["paths"]
    store, ingestor = _ingestor(paths)
    ingestor.ingest_path(_write_text(Path(paths.home)))

    result = store.search("inclusiva termoquesemduvidaexiste")
    assert result["relaxed"] is True
    assert result["results"]
    assert all("texto" in item["matched_by"] for item in result["results"])
