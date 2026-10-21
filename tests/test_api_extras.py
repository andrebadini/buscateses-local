"""Tests for integration, data-folder and update API additions."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from buscateses.api import create_app, routes_app, routes_integrations, routes_status
from buscateses.config import Settings
from buscateses.integrations import IntegrationResult
from tests.conftest import TEST_PORT, FakeEmbedder, auth_headers


def test_integrations_listing_and_errors(app_env) -> None:
    app = create_app(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        listing = client.get("/api/integrations")
        assert listing.status_code == 200
        ids = {row["id"] for row in listing.json()}
        assert "claude_desktop" in ids and "vscode" in ids
        assert "chatgpt" not in ids and "claude_web" not in ids
        assert client.post(
            "/api/integrations/inexistente/connect", headers=auth_headers()
        ).status_code == 400


def test_integration_connect_uses_result(app_env, monkeypatch) -> None:
    result = IntegrationResult(
        id="cursor",
        connected=True,
        changed=True,
        message="Conectado.",
        config_path="/tmp/mcp.json",
        kind="auto",
    )
    monkeypatch.setattr(
        routes_integrations, "connect", lambda client, **_kwargs: result
    )
    monkeypatch.setattr(routes_integrations, "disconnect", lambda client: result)
    app = create_app(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        connected = client.post(
            "/api/integrations/cursor/connect", headers=auth_headers()
        )
        assert connected.status_code == 200
        assert connected.json()["connected"] is True
        disconnected = client.post(
            "/api/integrations/cursor/disconnect", headers=auth_headers()
        )
        assert disconnected.status_code == 200


def test_open_data_dir(app_env, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(routes_app, "open_folder", lambda path: opened.append(str(path)))
    app = create_app(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        response = client.post("/api/app/open-data-dir", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["path"] == str(app_env["paths"].home)
        assert opened == [str(app_env["paths"].home)]

        def broken(path) -> None:
            raise OSError("sem gerenciador de arquivos")

        monkeypatch.setattr(routes_app, "open_folder", broken)
        failed = client.post("/api/app/open-data-dir", headers=auth_headers())
        assert failed.status_code == 500
        assert "pasta de dados" in failed.json()["detail"]


def test_settings_never_expose_secrets(app_env) -> None:
    settings = replace(
        app_env["settings"],
        embedding=replace(app_env["settings"].embedding, api_key="segredo"),
        mcp_http=replace(app_env["settings"].mcp_http, token="token-secreto"),
    )
    app = create_app(app_env["paths"], settings, embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        payload = client.get("/api/settings").json()
        assert "api_key" not in payload["embedding"]
        assert "token" not in payload["mcp_http"]
        assert payload["embedding"]["has_api_key"] is True
        assert payload["mcp_http"]["token_set"] is True


def test_settings_put_only_changes_edited_fields(app_env) -> None:
    paths = app_env["paths"].ensure()
    paths.config_file.write_text(
        json.dumps(
            {
                "server": {"port": 9000},
                "embedding": {"engine": "local", "api_key": "guardada"},
                "mcp_http": {"token": "token-no-disco"},
                "updates": {"check_app": True, "check_catalog": True},
            }
        ),
        encoding="utf-8",
    )
    # Runtime settings may carry a fallback port and environment overrides.
    runtime = replace(
        Settings.load(paths),
        server=replace(Settings.load(paths).server, port=65000),
        dataset_url="https://espelho.example",
    )
    app = create_app(paths, runtime, embedder=FakeEmbedder())
    with TestClient(app, base_url="http://127.0.0.1:65000") as client:
        response = client.put(
            "/api/settings",
            json={
                "updates": {"check_app": False},
                "embedding": {"api_key": ""},
                "mcp_http": {"token": ""},
            },
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "api_key" not in data["embedding"]
        assert data["mcp_http"]["token_set"] is True
        assert data["embedding"]["has_api_key"] is True

    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["updates"]["check_app"] is False
    assert on_disk["updates"]["check_catalog"] is True
    assert on_disk["server"]["port"] == 9000
    assert on_disk["embedding"]["api_key"] == "guardada"
    assert on_disk["mcp_http"]["token"] == "token-no-disco"
    assert "dataset_url" not in on_disk

    with TestClient(app, base_url="http://127.0.0.1:65000") as client:
        changed = client.put(
            "/api/settings",
            json={
                "embedding": {"api_key": "nova"},
                "mcp_http": {"token": "novo-token"},
            },
            headers=auth_headers(),
        )
        assert changed.status_code == 200
    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["embedding"]["api_key"] == "nova"
    assert on_disk["mcp_http"]["token"] == "novo-token"
    assert on_disk["updates"]["check_app"] is False


def test_settings_drop_api_key_when_embedding_url_changes(app_env) -> None:
    paths = app_env["paths"].ensure()

    def write_config(base_url: str, api_key: str) -> None:
        paths.config_file.write_text(
            json.dumps(
                {
                    "embedding": {
                        "engine": "openai",
                        "base_url": base_url,
                        "model": "modelo",
                        "api_key": api_key,
                    }
                }
            ),
            encoding="utf-8",
        )

    write_config("https://antigo.example/v1", "guardada")
    app = create_app(paths, app_env["settings"], embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        changed = client.put(
            "/api/settings",
            json={"embedding": {"base_url": "https://novo.example/v1"}},
            headers=auth_headers(),
        )
        assert changed.status_code == 200
        assert changed.json()["embedding"]["has_api_key"] is False

    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["embedding"]["api_key"] == ""

    # Keeping the same URL preserves the stored secret.
    write_config("https://antigo.example/v1", "guardada")
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        same = client.put(
            "/api/settings",
            json={"embedding": {"base_url": "https://antigo.example/v1"}},
            headers=auth_headers(),
        )
        assert same.json()["embedding"]["has_api_key"] is True
    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["embedding"]["api_key"] == "guardada"

    # A new key supplied together with the new URL is stored normally.
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        fresh = client.put(
            "/api/settings",
            json={
                "embedding": {
                    "base_url": "https://outro.example/v1",
                    "api_key": "nova",
                }
            },
            headers=auth_headers(),
        )
        assert fresh.json()["embedding"]["has_api_key"] is True
    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["embedding"]["api_key"] == "nova"


def test_status_includes_app_update(app_env, monkeypatch) -> None:
    monkeypatch.setattr(
        routes_status.updates,
        "get_status",
        lambda settings: {"available": True, "latest": "9.0.0", "url": "https://x"},
    )
    app = create_app(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
        payload = client.get("/api/status").json()
        assert payload["app"]["pid"] > 0
        assert payload["app_update"]["latest"] == "9.0.0"
        assert payload["paths"]["home"] == str(app_env["paths"].home)
        assert set(payload["library"]) == {"documents", "chunks", "pending"}
