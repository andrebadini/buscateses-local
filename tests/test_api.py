from __future__ import annotations

import json
import time

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from buscateses.api import create_app
from buscateses.catalog.installer import CatalogInstaller
from tests.conftest import TEST_PORT, auth_headers


def _wait_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/jobs/{job_id}")
        assert payload.status_code == 200
        job = payload.json()
        if job["status"] in {"done", "error", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"tarefa {job_id} não terminou a tempo")


@pytest.fixture
def installed_client(app_env):
    """App backed by a real installed release (no catalog injection)."""
    CatalogInstaller(app_env["paths"], app_env["settings"]).install()
    app = create_app(
        app_env["paths"],
        app_env["settings"],
        embedder=app_env["dataset"].embedder,
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as test_client:
        yield test_client


def test_status_without_catalog(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["app"]["version"] == "1.1.0"
    assert payload["catalog"]["installed"] is False
    assert payload["catalog"]["records"] == 0
    assert payload["model"]["ready"] is True
    assert payload["model"]["engine"] == "local"
    assert payload["library"] == {"documents": 0, "chunks": 0, "pending": 0}
    assert payload["setup_required"] is True
    assert "catalog_missing" in payload["warnings"]


def test_catalog_endpoints_are_unavailable_before_setup(client: TestClient) -> None:
    search = client.get("/api/search", params={"q": "inclusiva"})
    assert search.status_code == 503
    assert search.json()["detail"] == "O catálogo ainda não foi instalado."
    facets = client.get("/api/facets")
    assert facets.status_code == 200
    assert facets.json()["degree"] == []
    export = client.get("/api/export", params={"q": "inclusiva"})
    assert export.status_code == 503


def test_health_and_static_interface(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "BuscaTeses" in index.text
    missing = client.get("/api/nao-existe")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")
    assert missing.json()["detail"]


def test_index_sets_restrictive_content_security_policy(client: TestClient) -> None:
    from buscateses.api import INDEX_CSP

    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == INDEX_CSP
        assert "script-src 'self' https://vlibras.gov.br" in INDEX_CSP
        assert "object-src 'none'" in INDEX_CSP
        assert "frame-ancestors 'none'" in INDEX_CSP

    text = client.get("/").text
    # Scripts must be external for ``script-src 'self'`` to hold, and the
    # third-party widget must not be part of the initial document.
    assert "<script>" not in text
    assert "vw-access-button" not in text
    assert 'id="libras-toggle"' in text
    assert 'aria-pressed="false"' in text


def test_setup_job_installs_catalog(client: TestClient) -> None:
    response = client.post(
        "/api/setup",
        json={"components": ["catalog"]},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    job = _wait_job(client, job_id)
    assert job["status"] == "done", job
    assert job["progress"] == 1.0

    status = client.get("/api/status").json()
    assert status["catalog"]["installed"] is True
    assert status["catalog"]["records"] == 60
    assert status["catalog"]["version"] == "2026.10.21"
    assert status["setup_required"] is False
    assert "catalog_missing" not in status["warnings"]

    jobs = client.get("/api/jobs").json()
    assert isinstance(jobs, list)
    assert jobs[0]["id"] == job_id
    assert client.get("/api/jobs", params={"active": 1}).json() == []
    finished = client.get("/api/jobs", params={"active": 0}).json()
    assert [item["id"] for item in finished] == [job_id]


def test_search_detail_facets_and_similar(installed_client: TestClient) -> None:
    response = installed_client.get(
        "/api/search",
        params={"q": "inclusiva", "mode": "hybrid", "page_size": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode_used"] == "hybrid"
    assert payload["total"] > 0
    assert len(payload["results"]) == 5
    assert payload["results"][0]["id"].startswith("capes:")
    assert "matched_by" in payload["results"][0]
    assert payload["facets"]["degree"]

    filtered = installed_client.get(
        "/api/search",
        params=[("q", "inclusiva"), ("mode", "text"), ("uf", "MG"), ("uf", "SP")],
    )
    assert filtered.status_code == 200
    assert all(item["uf"] in {"MG", "SP"} for item in filtered.json()["results"])

    detail = installed_client.get("/api/theses/capes:0001")
    assert detail.status_code == 200
    assert detail.json()["title"].startswith("Estudo sobre")
    missing = installed_client.get("/api/theses/capes:9999")
    assert missing.status_code == 404

    similar = installed_client.get("/api/theses/capes:0001/similar", params={"k": 3})
    assert similar.status_code == 200
    assert len(similar.json()) == 3
    assert all(item["id"] != "capes:0001" for item in similar.json())

    facets = installed_client.get("/api/facets")
    assert facets.status_code == 200
    assert {item["value"] for item in facets.json()["uf"]} == {"MG", "SP", "RJ", "DF"}


def test_graceful_fallback_without_local_model(app_env) -> None:
    CatalogInstaller(app_env["paths"], app_env["settings"]).install()
    app = create_app(app_env["paths"], app_env["settings"])
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as test_client:
        status = test_client.get("/api/status").json()
        assert status["catalog"]["installed"] is True
        assert status["model"]["ready"] is False
        assert status["model"]["downloading"] is False
        assert status["setup_required"] is True
        assert "model_missing" in status["warnings"]
        assert "semantic_unavailable" in status["warnings"]

        response = test_client.get(
            "/api/search", params={"q": "inclusiva", "mode": "hybrid"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["mode_used"] == "text"
        assert payload["warnings"] == ["semantic_unavailable"]
        assert payload["results"]

        probe = test_client.post(
            "/api/settings/test-embedding", headers=auth_headers()
        )
        assert probe.status_code == 200
        assert probe.json()["ok"] is False
        assert probe.json()["ready"] is False


def test_search_errors_are_json(installed_client: TestClient) -> None:
    empty = installed_client.get("/api/search", params={"q": "  "})
    assert empty.status_code == 400
    assert empty.json()["detail"] == "Digite uma consulta para buscar."
    invalid = installed_client.get("/api/search", params={"q": "x", "mode": "magic"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Modo de busca inválido."
    bad_page = installed_client.get("/api/search", params={"q": "x", "page_size": 1000})
    assert bad_page.status_code == 400


def test_export_formats(installed_client: TestClient) -> None:
    csv_response = installed_client.get(
        "/api/export",
        params={"q": "inclusiva", "mode": "text", "format": "csv", "page_size": 5},
    )
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment; filename=\"buscateses.csv\"" == csv_response.headers[
        "content-disposition"
    ]
    assert csv_response.headers["content-type"].startswith("text/csv")

    bib = installed_client.get(
        "/api/export",
        params=[("ids", "capes:0001"), ("ids", "capes:0002"), ("format", "bibtex")],
    )
    assert bib.status_code == 200
    assert bib.text.count("@") == 2
    assert bib.headers["content-type"].startswith("application/x-bibtex")

    ris = installed_client.get(
        "/api/export", params={"ids": "capes:0001,capes:0002", "format": "ris"}
    )
    assert ris.status_code == 200
    assert ris.text.count("TY  - THES") == 2

    abnt = installed_client.get(
        "/api/export", params={"q": "inclusiva", "mode": "text", "format": "abnt"}
    )
    assert abnt.status_code == 200
    assert abnt.headers["content-type"].startswith("text/plain")

    invalid = installed_client.get(
        "/api/export", params={"ids": "capes:0001", "format": "docx"}
    )
    assert invalid.status_code == 400
    unknown = installed_client.get(
        "/api/export", params={"ids": "capes:9999", "format": "csv"}
    )
    assert unknown.status_code == 404


def test_favorites_crud(installed_client: TestClient) -> None:
    added = installed_client.post(
        "/api/favorites",
        json={"id": "capes:0001", "note": "importante"},
        headers=auth_headers(),
    )
    assert added.status_code == 200
    assert added.json() == {"ok": True, "added": True, "id": "capes:0001"}

    duplicate = installed_client.post(
        "/api/favorites",
        json={"id": "capes:0001", "note": "revisar"},
        headers=auth_headers(),
    )
    assert duplicate.json()["added"] is False

    listed = installed_client.get("/api/favorites")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["note"] == "revisar"

    removed = installed_client.delete(
        "/api/favorites/capes:0001", headers=auth_headers()
    )
    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    assert installed_client.get("/api/favorites").json() == []

    invalid = installed_client.post(
        "/api/favorites", json={"id": "   "}, headers=auth_headers()
    )
    assert invalid.status_code == 400


def test_settings_and_embedding_test(installed_client: TestClient) -> None:
    current = installed_client.get("/api/settings")
    assert current.status_code == 200
    assert current.json()["embedding"]["engine"] == "local"
    assert current.json()["server"]["port"] == TEST_PORT

    probe = installed_client.post(
        "/api/settings/test-embedding",
        json={"text": "educação inclusiva"},
        headers=auth_headers(),
    )
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    assert probe.json()["dimensions"] == 768

    updated = installed_client.put(
        "/api/settings",
        json={"updates": {"check_catalog": False}, "onboarding_done": True},
        headers=auth_headers(),
    )
    assert updated.status_code == 200
    assert updated.json()["updates"]["check_catalog"] is False
    assert updated.json()["onboarding_done"] is True
    assert updated.json()["embedding"]["engine"] == "local"

    saved = installed_client.get("/api/settings").json()
    assert saved["updates"]["check_catalog"] is False

    invalid = installed_client.put(
        "/api/settings",
        json={"embedding": {"engine": "inexistente"}},
        headers=auth_headers(),
    )
    assert invalid.status_code == 400
    assert "Motor de embeddings" in invalid.json()["detail"]


def test_catalog_update_check(installed_client: TestClient) -> None:
    response = installed_client.get("/api/catalog/update")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["current"] == "2026.10.21"
    assert payload["latest"] == "2026.10.21"


def test_security_rejects_bad_host(client: TestClient) -> None:
    response = client.get("/api/status", headers={"host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Acesso restrito ao computador local."
    loopback = client.get("/api/status", headers={"host": f"localhost:{TEST_PORT}"})
    assert loopback.status_code == 200


def test_security_requires_csrf_header(installed_client: TestClient) -> None:
    response = installed_client.post("/api/favorites", json={"id": "capes:0001"})
    assert response.status_code == 403
    assert "segurança" in response.json()["detail"]
    write = installed_client.put("/api/settings", json={"onboarding_done": True})
    assert write.status_code == 403
    get = installed_client.get("/api/favorites")
    assert get.status_code == 200


def test_security_rejects_foreign_origin(installed_client: TestClient) -> None:
    headers = {**auth_headers(), "origin": "http://evil.example"}
    response = installed_client.post(
        "/api/favorites", json={"id": "capes:0001"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Origem da requisição não permitida."
    allowed = installed_client.post(
        "/api/favorites",
        json={"id": "capes:0001"},
        headers={**auth_headers(), "origin": f"http://127.0.0.1:{TEST_PORT}"},
    )
    assert allowed.status_code == 200


def test_setup_validation(client: TestClient) -> None:
    empty = client.post(
        "/api/setup", json={"components": []}, headers=auth_headers()
    )
    assert empty.status_code == 400
    unknown = client.post(
        "/api/setup", json={"components": ["desconhecido"]}, headers=auth_headers()
    )
    assert unknown.status_code == 400
    malformed = client.post(
        "/api/setup", json={"components": "catalog"}, headers=auth_headers()
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"] == "Parâmetros inválidos na requisição."


def test_quit_callback(app_env) -> None:
    calls: list[str] = []
    app = create_app(
        app_env["paths"],
        app_env["settings"],
        quit_callback=lambda: calls.append("quit"),
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as test_client:
        response = test_client.post("/api/app/quit", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["ok"] is True
    assert calls == ["quit"]


def test_extra_router_extension_point(app_env) -> None:
    router = APIRouter()

    @router.get("/api/extra")
    def extra() -> dict:
        return {"extra": True}

    app = create_app(
        app_env["paths"], app_env["settings"], extra_routers=[router]
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as test_client:
        assert test_client.get("/api/extra").json() == {"extra": True}


def test_settings_file_is_persisted(installed_client: TestClient, app_env) -> None:
    installed_client.put(
        "/api/settings",
        json={"updates": {"check_app": False}},
        headers=auth_headers(),
    )
    payload = json.loads(app_env["paths"].config_file.read_text(encoding="utf-8"))
    assert payload["updates"]["check_app"] is False
    assert "dataset_url" not in payload
