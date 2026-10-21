"""Tests for the read-only MCP server (SPEC §9)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from buscateses.config import get_settings
from buscateses.library.ingest import LibraryIngestor
from buscateses.library.store import LibraryStore
from buscateses.mcp_server import (
    INSTRUCTIONS,
    _BearerAuthMiddleware,
    build_http_app,
    create_server,
    ensure_http_token,
)
from tests.conftest import FakeEmbedder

TEXT = (
    "A pesquisa analisa educação inclusiva e formação de professores na escola "
    "pública brasileira. "
) * 12


def _run(server, tool: str, arguments: dict[str, Any] | None = None):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def exercise() -> tuple[Any, Any]:
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
            result = await session.call_tool(tool, arguments or {})
            return tools, result

    return asyncio.run(exercise())


def _library(app_env) -> tuple[LibraryStore, dict[str, Any]]:
    store = LibraryStore(app_env["paths"])
    ingestor = LibraryIngestor(app_env["paths"], FakeEmbedder(), store=store)
    source = Path(app_env["paths"].home) / "pesquisa.txt"
    source.write_text(TEXT, encoding="utf-8")
    outcome = ingestor.ingest_path(source)
    return store, store.get_document(outcome.doc_id) or {}


def test_tools_are_read_only_with_structured_output(app_env) -> None:
    server = create_server(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    tools, _ = _run(server, "catalog_status")
    names = [tool.name for tool in tools.tools]
    assert names == [
        "search_theses",
        "get_thesis",
        "similar_theses",
        "search_library",
        "list_library_documents",
        "get_library_passage",
        "catalog_status",
    ]
    for tool in tools.tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema
    assert len(INSTRUCTIONS) <= 512


def test_remote_mode_hides_library_tools(app_env) -> None:
    server = create_server(
        app_env["paths"],
        app_env["settings"],
        embedder=FakeEmbedder(),
        include_library=False,
        streamable_http_path="/mcp/segredo",
        stateless_http=True,
        json_response=True,
        enable_dns_rebinding_protection=False,
        remote=True,
    )
    tools, _ = _run(server, "catalog_status")
    names = [tool.name for tool in tools.tools]
    assert names == ["search_theses", "get_thesis", "similar_theses", "catalog_status"]
    assert "biblioteca" not in (tools.tools[-1].description or "").lower()

    from buscateses.mcp_server import _instructions

    instructions = _instructions(False, remote=True)
    assert "biblioteca" not in instructions.lower()
    assert len(instructions) <= 512


def test_missing_catalog_returns_clear_error(app_env) -> None:
    server = create_server(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    _, result = _run(server, "search_theses", {"query": "educação"})
    assert result.isError is True
    assert "Abra o app BuscaTeses" in result.content[0].text


def test_catalog_tools_with_injected_catalog(installed) -> None:
    server = create_server(
        installed["paths"],
        installed["settings"],
        embedder=installed["dataset"].embedder,
        catalog=installed["catalog"],
    )
    _, search = _run(server, "search_theses", {"query": "educação inclusiva", "limit": 3})
    assert search.isError is False
    payload = search.structuredContent
    assert payload["mode_used"] == "hybrid"
    assert payload["results"]
    identifier = payload["results"][0]["id"]

    _, detail = _run(server, "get_thesis", {"id": identifier})
    assert detail.isError is False
    assert detail.structuredContent["id"] == identifier

    _, similar = _run(server, "similar_theses", {"id": identifier, "limit": 2})
    assert similar.isError is False
    assert len(similar.structuredContent["results"]) <= 2

    _, invalid = _run(server, "search_theses", {"query": "x", "limit": 999})
    assert invalid.isError is True
    assert "limite" in invalid.content[0].text.lower()

    _, missing = _run(server, "get_thesis", {"id": "capes:inexistente"})
    assert missing.isError is True


def test_library_tools(app_env) -> None:
    store, document = _library(app_env)
    server = create_server(
        app_env["paths"],
        app_env["settings"],
        embedder=FakeEmbedder(),
        store=store,
    )
    _, listing = _run(server, "list_library_documents")
    assert listing.isError is False
    documents = listing.structuredContent["documents"]
    assert documents[0]["id"] == document["id"]
    assert documents[0]["embedded"] is True

    _, search = _run(server, "search_library", {"query": "educação inclusiva"})
    assert search.isError is False
    hit = search.structuredContent["results"][0]
    assert hit["doc_id"] == document["id"]

    _, passage = _run(
        server,
        "get_library_passage",
        {"chunk_id": hit["chunk_id"], "context_chunks": 0},
    )
    assert passage.isError is False
    assert passage.structuredContent["text"]
    assert passage.structuredContent["chunks"][0]["is_target"] is True

    _, bad_context = _run(
        server, "get_library_passage", {"chunk_id": hit["chunk_id"], "context_chunks": 9}
    )
    assert bad_context.isError is True
    assert "contexto" in bad_context.content[0].text.lower()

    _, status = _run(server, "catalog_status")
    assert status.structuredContent["library_documents"] == 1
    assert status.structuredContent["library_chunks"] == document["chunks"]


def test_library_missing_message(app_env) -> None:
    server = create_server(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    _, result = _run(server, "list_library_documents")
    assert result.isError is True
    assert "biblioteca ainda está vazia" in result.content[0].text


def test_http_bearer_protection(app_env) -> None:
    server = create_server(app_env["paths"], app_env["settings"], embedder=FakeEmbedder())
    app = build_http_app(server, "segredo")
    with TestClient(app) as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer errado"}
        ).status_code == 401
        assert client.post(
            "/mcp", json={}, headers={"Authorization": b"Bearer inv\xe1lido"}
        ).status_code == 401
        response = client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer segredo"}
        )
        assert response.status_code != 401
        assert response.headers.get("www-authenticate") is None


def test_bearer_middleware_forwards_valid_token() -> None:
    calls: list[dict] = []

    async def inner(scope, receive, send) -> None:  # noqa: ANN001
        calls.append(scope)

    middleware = _BearerAuthMiddleware(inner, "abc")
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer abc")],
    }
    asyncio.run(middleware(scope, None, send))
    assert calls == [scope]
    assert sent == []


def test_transport_security_restricts_hosts_to_loopback(app_env) -> None:
    settings = replace(
        app_env["settings"],
        mcp_http=replace(app_env["settings"].mcp_http, port=8765),
    )
    server = create_server(app_env["paths"], settings, embedder=FakeEmbedder())
    security = server.settings.transport_security
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:8765", "localhost:8765"]
    assert security.allowed_origins == [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
    ]


def test_transport_security_accepts_configured_tunnel_hosts(app_env) -> None:
    settings = replace(
        app_env["settings"],
        mcp_http=replace(
            app_env["settings"].mcp_http,
            port=8765,
            allowed_hosts=("minha-maquina.tailnet.ts.net", "tunel.example:8443"),
        ),
    )
    server = create_server(app_env["paths"], settings, embedder=FakeEmbedder())
    security = server.settings.transport_security
    assert "minha-maquina.tailnet.ts.net" in security.allowed_hosts
    assert "minha-maquina.tailnet.ts.net:443" in security.allowed_hosts
    assert "tunel.example:8443" in security.allowed_hosts
    assert "https://minha-maquina.tailnet.ts.net" in security.allowed_origins
    assert "https://minha-maquina.tailnet.ts.net:443" in security.allowed_origins
    assert "https://tunel.example:8443" in security.allowed_origins
    # Loopback is still accepted.
    assert "127.0.0.1:8765" in security.allowed_hosts


def test_allowed_hosts_setting_round_trip() -> None:
    from buscateses.config import ConfigError, Settings

    settings = Settings.from_dict(
        {
            "mcp_http": {
                "allowed_hosts": [
                    "A.ts.net",
                    "b.ts.net:8443",
                    "a.ts.net",
                    " ",
                ]
            }
        },
        apply_env=False,
    )
    assert settings.mcp_http.allowed_hosts == ("a.ts.net", "b.ts.net:8443")
    assert settings.to_dict()["mcp_http"]["allowed_hosts"] == [
        "a.ts.net",
        "b.ts.net:8443",
    ]

    for invalid in ("https://a.ts.net", "a.ts.net/caminho", "*", "a.ts.net:abc"):
        with pytest.raises(ConfigError):
            Settings.from_dict(
                {"mcp_http": {"allowed_hosts": [invalid]}}, apply_env=False
            )


def test_catalog_reopens_when_current_json_changes(installed) -> None:
    from buscateses.mcp_server import _Services

    services = _Services(
        installed["paths"],
        installed["settings"],
        embedder=installed["dataset"].embedder,
    )
    first = services.catalog_search()
    closed: list[bool] = []
    first.close = lambda: closed.append(True)

    assert services.catalog_search() is first
    current = installed["paths"].current_file
    bumped = current.stat().st_mtime_ns + 1_000_000
    os.utime(current, ns=(bumped, bumped))

    second = services.catalog_search()
    assert second is not first
    assert closed == [True]
    second.close()


def test_ensure_http_token_generates_and_persists(app_env) -> None:
    paths = app_env["paths"]
    settings = replace(
        app_env["settings"],
        mcp_http=replace(app_env["settings"].mcp_http, token=""),
    )
    resolved, token = ensure_http_token(paths, settings)
    assert token and len(token) > 20
    assert resolved.mcp_http.token == token
    assert get_settings(paths).mcp_http.token == token

    explicit, token_again = ensure_http_token(paths, resolved, token="meu-token")
    assert token_again == "meu-token"
    assert explicit.mcp_http.token == "meu-token"
    assert get_settings(paths).mcp_http.token == "meu-token"

    _, disabled = ensure_http_token(paths, explicit, allow_no_token=True)
    assert disabled is None
    assert get_settings(paths).mcp_http.token == "meu-token"

    # Runtime-only values are never frozen into config.json.
    runtime = replace(
        resolved,
        server=replace(resolved.server, port=65000),
        dataset_url="https://espelho.example",
    )
    ensure_http_token(paths, runtime, token="outro-token")
    disk = get_settings(paths)
    assert disk.mcp_http.token == "outro-token"
    assert disk.server.port == resolved.server.port
    assert disk.dataset_url == resolved.dataset_url


def test_run_http_binds_loopback_and_generates_token(app_env, monkeypatch) -> None:
    import uvicorn

    from buscateses import mcp_server

    calls: dict[str, Any] = {}

    def fake_run(app, **kwargs) -> None:
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(mcp_server, "_configure_logging", lambda paths: None)
    paths = app_env["paths"]
    settings = replace(
        app_env["settings"],
        mcp_http=replace(app_env["settings"].mcp_http, token="", port=8102),
    )
    mcp_server.run_http(paths, settings, port=8123)
    assert calls["kwargs"]["host"] == "127.0.0.1"
    assert calls["kwargs"]["port"] == 8123
    assert get_settings(paths).mcp_http.token
    # Only the generated token is persisted; the runtime port is not.
    assert get_settings(paths).mcp_http.port == 8102

    with pytest.raises(ValueError, match="porta"):
        mcp_server.run_http(paths, settings, port=0)
