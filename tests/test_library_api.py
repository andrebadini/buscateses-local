"""HTTP tests for the personal library API (SPEC §8)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from buscateses.api import create_app, routes_library
from buscateses.library.ingest import LibraryIngestor
from buscateses.library.store import LibraryError
from tests.conftest import TEST_PORT, FakeEmbedder, auth_headers

TEXT = (
    "Este documento discute educação inclusiva, políticas públicas e formação "
    "docente. A pesquisa usa métodos participativos. "
) * 10


def _client(app_env) -> TestClient:
    app = create_app(
        app_env["paths"],
        app_env["settings"],
        embedder=FakeEmbedder(),
    )
    return TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}")


def _upload(client: TestClient, name: str, content: bytes) -> dict:
    response = client.post(
        "/api/library/upload",
        files={"files": (name, content, "text/plain")},
        headers=auth_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait(client: TestClient, job_id: str) -> dict:
    return client.app.state.ctx.jobs.wait(job_id, timeout=10)


def test_upload_ingest_search_and_file(app_env) -> None:
    with _client(app_env) as client:
        payload = _upload(client, "educação.txt", TEXT.encode("utf-8"))
        assert len(payload["jobs"]) == 1
        assert payload["skipped"] == []
        job = _wait(client, payload["jobs"][0]["job_id"])
        assert job["status"] == "done", job
        assert job["kind"] == "library"

        documents = client.get("/api/library/documents").json()
        assert len(documents) == 1
        document = documents[0]
        assert document["title"] == "Educação"
        assert document["status"] == "ready"
        assert document["embedded"] is True

        one = client.get(f"/api/library/documents/{document['id']}")
        assert one.status_code == 200
        assert one.json()["id"] == document["id"]

        search = client.get("/api/library/search", params={"q": "educação inclusiva"})
        assert search.status_code == 200
        result = search.json()
        assert result["mode_used"] == "hybrid"
        assert result["results"]
        chunk_id = result["results"][0]["chunk_id"]

        file_response = client.get(f"/api/library/documents/{document['id']}/file")
        assert file_response.status_code == 200
        assert file_response.headers["content-type"].startswith("text/plain")
        disposition = file_response.headers["content-disposition"]
        assert disposition.startswith("inline;")
        assert "filename*=UTF-8''" in disposition
        assert file_response.content == TEXT.encode("utf-8")

        jobs = client.get("/api/library/jobs").json()
        assert [item["id"] for item in jobs] == [payload["jobs"][0]["job_id"]]
        assert chunk_id > 0

        status = client.get("/api/status").json()
        assert status["library"]["documents"] == 1
        assert status["library"]["chunks"] == document["chunks"]


def _add_stored_document(client: TestClient, doc_id: str, kind: str, filename: str) -> None:
    """Insert a ready document and its stored file without running extraction."""
    state = client.app.state.ctx
    digest = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()
    state.library.create_document(
        doc_id=doc_id,
        sha256=digest,
        filename=filename,
        kind=kind,
        added_at="2026-01-01T00:00:00+00:00",
        status="ready",
    )
    state.library.file_path_for(digest, kind).write_bytes(b"conteudo")


def test_document_file_headers_cannot_execute_on_the_app_origin(app_env) -> None:
    with _client(app_env) as client:
        kinds = {
            "txt": "text/plain; charset=utf-8",
            "md": "text/plain; charset=utf-8",
            "html": "text/plain; charset=utf-8",
            "pdf": "application/pdf",
            "epub": "application/epub+zip",
            "docx": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        }
        for kind in kinds:
            _add_stored_document(client, f"doc-{kind}", kind, f"arquivo.{kind}")

        for kind, media_type in kinds.items():
            response = client.get(f"/api/library/documents/doc-{kind}/file")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(media_type)
            assert response.headers["x-content-type-options"] == "nosniff"
            disposition = response.headers["content-disposition"]
            if kind in {"epub", "docx"}:
                assert disposition.startswith("attachment;")
            else:
                assert disposition.startswith("inline;")
            if kind == "pdf":
                # Chromium/WebView2 refuse to render PDFs served with
                # ``Content-Security-Policy: sandbox``.
                assert "content-security-policy" not in response.headers
            else:
                assert (
                    response.headers["content-security-policy"]
                    == "sandbox; default-src 'none'"
                )

        html = client.get("/api/library/documents/doc-html/file")
        assert html.text == "conteudo"


def test_upload_rejects_oversized_request_before_reading(app_env, monkeypatch) -> None:
    monkeypatch.setattr(routes_library, "MAX_REQUEST_BYTES", 10)
    with _client(app_env) as client:
        response = client.post(
            "/api/library/upload",
            files={"files": ("a.txt", b"texto", "text/plain")},
            headers=auth_headers(),
        )
        assert response.status_code == 413
        assert "520 MB" in response.json()["detail"]
        assert client.get("/api/library/documents").json() == []


def test_upload_size_middleware_rejects_before_multipart_parsing(
    app_env, monkeypatch
) -> None:
    """The header guard runs even for a body that is not valid multipart."""
    monkeypatch.setattr(routes_library, "MAX_REQUEST_BYTES", 10)
    with _client(app_env) as client:
        response = client.post(
            "/api/library/upload",
            content=b"nao-e-multipart",
            headers={
                **auth_headers(),
                "Content-Type": "application/octet-stream",
                "Content-Length": "500",
            },
        )
        assert response.status_code == 413
        assert "520 MB" in response.json()["detail"]


def test_upload_skips_duplicates_empty_and_unsupported(app_env) -> None:
    with _client(app_env) as client:
        first = _upload(client, "a.txt", TEXT.encode("utf-8"))
        _wait(client, first["jobs"][0]["job_id"])
        duplicate = _upload(client, "b.txt", TEXT.encode("utf-8"))
        assert duplicate["jobs"] == []
        assert "já está na biblioteca" in duplicate["skipped"][0]["reason"]

        empty = _upload(client, "vazio.txt", b"")
        assert "vazio" in empty["skipped"][0]["reason"]

        bad = client.post(
            "/api/library/upload",
            files={"files": ("programa.exe", b"MZ", "application/octet-stream")},
            headers=auth_headers(),
        )
        assert bad.status_code == 200
        assert "Formato não suportado" in bad.json()["skipped"][0]["reason"]
        assert len(client.get("/api/library/documents").json()) == 1


def test_upload_deduplicates_within_same_request(app_env) -> None:
    with _client(app_env) as client:
        response = client.post(
            "/api/library/upload",
            files=[
                ("files", ("a.txt", TEXT.encode("utf-8"), "text/plain")),
                ("files", ("b.txt", TEXT.encode("utf-8"), "text/plain")),
            ],
            headers=auth_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 1
        assert "já está na biblioteca" in payload["skipped"][0]["reason"]
        _wait(client, payload["jobs"][0]["job_id"])
        assert len(client.get("/api/library/documents").json()) == 1


def test_upload_respects_size_limit(app_env, monkeypatch) -> None:
    monkeypatch.setattr(routes_library, "MAX_FILE_BYTES", 8)
    with _client(app_env) as client:
        payload = _upload(client, "grande.txt", b"123456789")
        assert payload["jobs"] == []
        assert "maior que o limite" in payload["skipped"][0]["reason"]


def test_update_delete_and_missing_documents(app_env) -> None:
    with _client(app_env) as client:
        payload = _upload(client, "nota.txt", TEXT.encode("utf-8"))
        _wait(client, payload["jobs"][0]["job_id"])
        doc_id = client.get("/api/library/documents").json()[0]["id"]

        updated = client.patch(
            f"/api/library/documents/{doc_id}",
            json={"title": "Título novo", "author": "Autora", "year": 2024},
            headers=auth_headers(),
        )
        assert updated.status_code == 200
        data = updated.json()
        assert data["title"] == "Título novo"
        assert data["author"] == "Autora"
        assert data["year"] == 2024

        invalid_year = client.patch(
            f"/api/library/documents/{doc_id}",
            json={"year": 50},
            headers=auth_headers(),
        )
        assert invalid_year.status_code == 400
        missing = client.patch(
            "/api/library/documents/nao-existe",
            json={"title": "x"},
            headers=auth_headers(),
        )
        assert missing.status_code == 404

        assert client.delete(
            f"/api/library/documents/{doc_id}", headers=auth_headers()
        ).status_code == 200
        assert client.get(f"/api/library/documents/{doc_id}").status_code == 404
        assert client.delete(
            f"/api/library/documents/{doc_id}", headers=auth_headers()
        ).status_code == 404
        assert client.get(f"/api/library/documents/{doc_id}/file").status_code == 404


def test_search_validation_and_missing_document_filter(app_env) -> None:
    with _client(app_env) as client:
        assert client.get("/api/library/search", params={"q": ""}).status_code == 422
        assert client.get(
            "/api/library/search", params={"q": "x", "page": 0}
        ).status_code == 422
        assert client.get(
            "/api/library/search", params={"q": "x", "page_size": 100}
        ).status_code == 422
        missing = client.get(
            "/api/library/search", params={"q": "x", "doc_id": "nao-existe"}
        )
        assert missing.status_code == 404
        assert "não encontrado" in missing.json()["detail"]


def test_reindex_job(app_env) -> None:
    paths = app_env["paths"]
    with _client(app_env) as client:
        state = client.app.state.ctx
        text_only = LibraryIngestor(paths, None, store=state.library)
        outcome = text_only.ingest_path(_source(paths.home / "sem-vetor.txt"))
        document = state.library.get_document(outcome.doc_id)
        assert document is not None and document["embedded"] is False

        response = client.post("/api/library/reindex", headers=auth_headers())
        assert response.status_code == 200
        job = _wait(client, response.json()["job_id"])
        assert job["status"] == "done", job
        assert "vetores" in job["message"]
        refreshed = client.get(f"/api/library/documents/{outcome.doc_id}").json()
        assert refreshed["embedded"] is True
        jobs = client.get("/api/library/jobs").json()
        assert any(item["kind"] == "library_reindex" for item in jobs)


def _source(path: Path) -> Path:
    path.write_text(TEXT, encoding="utf-8")
    return path


def test_upload_requires_csrf_header(app_env) -> None:
    with _client(app_env) as client:
        response = client.post(
            "/api/library/upload",
            files={"files": ("a.txt", b"texto", "text/plain")},
        )
        assert response.status_code == 403
        assert client.get("/api/library/documents").status_code == 200


def test_document_file_missing_raises_library_error(app_env) -> None:
    with _client(app_env) as client:
        state = client.app.state.ctx
        state.library.create_document(
            doc_id="x",
            sha256="a" * 64,
            filename="x.txt",
            kind="txt",
            added_at="2026-01-01T00:00:00+00:00",
            status="ready",
        )
        response = client.get("/api/library/documents/x/file")
        assert response.status_code == 404
        try:
            state.library.document_file("nao-existe")
        except LibraryError as exc:
            assert "encontrado" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("deveria falhar")
