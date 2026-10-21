"""Tests for the public MCP address (Cloudflare Quick Tunnel).

No real cloudflared binary and no network connection are used: a small Python
script prints a fixed trycloudflare.com URL and stays alive until stopped.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from buscateses import remote
from buscateses.api import create_app
from buscateses.config import Paths, RemoteSettings, Settings
from buscateses.jobs import JobManager
from buscateses.remote import (
    RemoteController,
    RemoteError,
    artifact_for,
    cloudflared_path,
    ensure_cloudflared,
    read_runtime_file,
    runtime_process_alive,
    save_remote_settings,
    write_runtime_file,
)
from tests.conftest import TEST_PORT, auth_headers

FAKE_URL = "https://fake-tunnel.trycloudflare.com"

_FAKE_SCRIPT = """\
import sys
import time
from pathlib import Path

Path(__file__ + ".args").write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
print("INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
print("INF |  https://fake-tunnel.trycloudflare.com  |", flush=True)
while True:
    time.sleep(0.2)
"""

_SILENT_SCRIPT = """\
import time
while True:
    time.sleep(0.2)
"""

_RPC_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@pytest.fixture
def fake_cloudflared(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_cloudflared.py"
    script.write_text(_FAKE_SCRIPT, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def silent_cloudflared(tmp_path: Path) -> list[str]:
    script = tmp_path / "silent_cloudflared.py"
    script.write_text(_SILENT_SCRIPT, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def controller(app_env, fake_cloudflared) -> RemoteController:
    instance = RemoteController(
        app_env["paths"],
        app_env["settings"],
        JobManager(),
        embedder=app_env["dataset"].embedder,
        cloudflared_command=fake_cloudflared,
        settings_saver=lambda settings: save_remote_settings(
            app_env["paths"], settings
        ),
        tunnel_timeout=10.0,
    )
    yield instance
    instance.stop()


def wait_state(
    controller: RemoteController,
    states: set[str],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Poll a controller until it reaches one of ``states``."""
    deadline = time.monotonic() + timeout
    status = controller.status()
    while time.monotonic() < deadline:
        status = controller.status()
        if status["state"] in states:
            return status
        time.sleep(0.1)
    raise AssertionError(f"estado {status['state']!r} não chegou a {states}: {status}")


def rpc(url: str, method: str, params: dict[str, Any] | None = None) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return httpx.post(url, json=body, headers=_RPC_HEADERS, timeout=10.0)


def local_base(controller: RemoteController) -> str:
    port = controller.port
    assert port is not None
    return f"http://127.0.0.1:{port}"


def secret_from(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def initialize_payload() -> dict[str, Any]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    }


def test_artifacts_are_pinned_per_platform() -> None:
    assert artifact_for("darwin", "aarch64").filename == "cloudflared-darwin-arm64.tgz"
    assert artifact_for("darwin", "x86_64").filename == "cloudflared-darwin-amd64.tgz"
    assert artifact_for("linux", "x86_64").filename == "cloudflared-linux-amd64"
    assert artifact_for("win32", "AMD64").filename == "cloudflared-windows-amd64.exe"
    artifact = artifact_for("darwin", "arm64")
    assert len(artifact.sha256) == 64
    assert artifact.size > 0
    with pytest.raises(RemoteError, match="não está disponível"):
        artifact_for("linux", "arm64")


def test_existing_binary_is_reused(app_env, monkeypatch) -> None:
    destination = cloudflared_path(app_env["paths"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"cloudflared")
    monkeypatch.setattr(remote, "_installed_matches", lambda path, artifact: True)
    assert ensure_cloudflared(app_env["paths"]) == destination


def test_archive_release_is_reused_after_verified_extraction(app_env, monkeypatch) -> None:
    """macOS ships a .tgz: the second call must not download it again."""
    import hashlib
    import io
    import tarfile

    binary = b"#!/bin/sh\necho cloudflared\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("cloudflared")
        info.size = len(binary)
        info.mode = 0o755
        bundle.addfile(info, io.BytesIO(binary))
    archive_bytes = buffer.getvalue()
    artifact = remote.CloudflaredArtifact(
        "cloudflared-darwin-arm64.tgz",
        hashlib.sha256(archive_bytes).hexdigest(),
        len(archive_bytes),
        archive=True,
    )
    monkeypatch.setattr(remote, "artifact_for", lambda *args, **kwargs: artifact)
    downloads: list[Path] = []

    def fake_download(url, target, **kwargs):
        downloads.append(Path(target))
        Path(target).write_bytes(archive_bytes)

    monkeypatch.setattr(remote, "download_file", fake_download)
    paths = app_env["paths"]
    first = ensure_cloudflared(paths)
    assert first.read_bytes() == binary
    assert remote.verified_marker_path(first).is_file()
    assert ensure_cloudflared(paths) == first
    assert len(downloads) == 1

    first.write_bytes(b"tampered")
    ensure_cloudflared(paths)
    assert len(downloads) == 2
    assert first.read_bytes() == binary


def test_start_captures_url_and_serves_json(controller, fake_cloudflared) -> None:
    assert controller.status()["state"] == "off"
    assert controller.status()["cloudflared_installed"] is True

    controller.start(include_library=False)
    status = wait_state(controller, {"online", "error"})
    assert status["state"] == "online", status["message"]
    assert status["url"] is not None
    assert status["url"].startswith(f"{FAKE_URL}/mcp/")
    assert status["include_library"] is False
    assert controller.process_id is not None

    args = Path(fake_cloudflared[-1] + ".args").read_text(encoding="utf-8")
    assert "--no-autoupdate" in args
    assert "--url" in args
    assert "127.0.0.1" in args

    secret = secret_from(status["url"])
    assert len(secret) >= 16
    base = local_base(controller)

    initialized = rpc(
        f"{base}/mcp/{secret}", "initialize", initialize_payload()
    )
    assert initialized.status_code == 200
    assert initialized.headers["content-type"].startswith("application/json")
    assert initialized.json()["result"]["serverInfo"]["name"] == "BuscaTeses"

    listed = rpc(f"{base}/mcp/{secret}", "tools/list", {})
    assert listed.status_code == 200
    assert listed.headers["content-type"].startswith("application/json")
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == [
        "search_theses",
        "get_thesis",
        "similar_theses",
        "catalog_status",
    ]

    for wrong in (f"{base}/mcp", f"{base}/mcp/segredo-errado", f"{base}/outro"):
        response = httpx.post(
            wrong,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": initialize_payload()},
            headers=_RPC_HEADERS,
            timeout=5.0,
        )
        assert response.status_code == 404


def test_library_tools_only_when_opted_in(controller) -> None:
    controller.start(include_library=True)
    status = wait_state(controller, {"online", "error"})
    assert status["state"] == "online", status["message"]
    base = local_base(controller)
    secret = secret_from(status["url"])

    listed = rpc(f"{base}/mcp/{secret}", "tools/list", {})
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == [
        "search_theses",
        "get_thesis",
        "similar_theses",
        "search_library",
        "list_library_documents",
        "get_library_passage",
        "catalog_status",
    ]


def test_rotate_changes_secret_and_old_path_stops_working(controller) -> None:
    controller.start(include_library=False)
    first = wait_state(controller, {"online", "error"})
    assert first["state"] == "online", first["message"]
    old_secret = secret_from(first["url"])
    assert controller.secret == old_secret

    rotated = controller.rotate()
    assert rotated["state"] in {"downloading", "starting", "online"}
    second = wait_state(controller, {"online", "error"})
    assert second["state"] == "online", second["message"]
    new_secret = secret_from(second["url"])
    assert new_secret != old_secret
    assert controller.secret == new_secret

    base = local_base(controller)
    assert rpc(f"{base}/mcp/{new_secret}", "tools/list", {}).status_code == 200
    assert rpc(f"{base}/mcp/{old_secret}", "tools/list", {}).status_code == 404


def test_stop_terminates_process_and_local_server(controller) -> None:
    controller.start(include_library=False)
    status = wait_state(controller, {"online", "error"})
    assert status["state"] == "online", status["message"]
    port = controller.port
    pid = controller.process_id
    assert port is not None and pid is not None

    controller.stop()
    stopped = controller.status()
    assert stopped["state"] == "off"
    assert stopped["url"] is None
    assert controller.process_id is None

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_tunnel_timeout_reports_actionable_error(
    app_env, silent_cloudflared
) -> None:
    instance = RemoteController(
        app_env["paths"],
        app_env["settings"],
        JobManager(),
        embedder=app_env["dataset"].embedder,
        cloudflared_command=silent_cloudflared,
        tunnel_timeout=0.6,
    )
    try:
        instance.start(include_library=False)
        status = wait_state(instance, {"online", "error"}, timeout=15.0)
        assert status["state"] == "error"
        assert "não respondeu" in status["message"]
        assert instance.process_id is None
    finally:
        instance.stop()


def test_start_rejects_a_second_address(controller) -> None:
    controller.start(include_library=False)
    wait_state(controller, {"online", "error"})
    with pytest.raises(RemoteError, match="já está"):
        controller.start(include_library=False)


def test_api_remote_endpoints(app_env, fake_cloudflared) -> None:
    app = create_app(
        app_env["paths"],
        app_env["settings"],
        embedder=app_env["dataset"].embedder,
    )
    controller = RemoteController(
        app_env["paths"],
        app_env["settings"],
        app.state.ctx.jobs,
        embedder=app_env["dataset"].embedder,
        cloudflared_command=fake_cloudflared,
        settings_saver=lambda settings: save_remote_settings(
            app_env["paths"], settings
        ),
        tunnel_timeout=10.0,
    )
    app.state.ctx.remote = controller
    try:
        with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
            initial = client.get("/api/remote").json()
            assert initial["state"] == "off"
            assert initial["cloudflared_installed"] is True
            assert set(initial) >= {
                "state",
                "url",
                "include_library",
                "message",
                "cloudflared_installed",
            }

            started = client.post(
                "/api/remote/start",
                json={"include_library": False},
                headers=auth_headers(),
            )
            assert started.status_code == 200
            assert started.json()["state"] in {"downloading", "starting", "online"}

            deadline = time.monotonic() + 15.0
            current = started.json()
            while time.monotonic() < deadline and current["state"] not in {
                "online",
                "error",
            }:
                time.sleep(0.1)
                current = client.get("/api/remote").json()
            assert current["state"] == "online", current["message"]
            assert current["url"].startswith(f"{FAKE_URL}/mcp/")

            conflict = client.post(
                "/api/remote/start",
                json={"include_library": False},
                headers=auth_headers(),
            )
            assert conflict.status_code == 409

            rotated = client.post("/api/remote/rotate", headers=auth_headers())
            assert rotated.status_code == 200
            deadline = time.monotonic() + 15.0
            rotated_status = rotated.json()
            while time.monotonic() < deadline and rotated_status["state"] not in {
                "online",
                "error",
            }:
                time.sleep(0.1)
                rotated_status = client.get("/api/remote").json()
            assert rotated_status["state"] == "online"
            assert rotated_status["url"] != current["url"]

            stopped = client.post("/api/remote/stop", headers=auth_headers())
            assert stopped.status_code == 200
            assert stopped.json()["state"] == "off"
    finally:
        controller.stop()


def test_settings_never_expose_the_remote_secret(app_env) -> None:
    secret = "segredo-de-teste-1234567890"
    settings = replace(
        app_env["settings"],
        remote=RemoteSettings(secret=secret, include_library=True),
    )
    settings.save(app_env["paths"])
    app = create_app(
        app_env["paths"],
        settings,
        embedder=app_env["dataset"].embedder,
    )
    try:
        with TestClient(app, base_url=f"http://127.0.0.1:{TEST_PORT}") as client:
            data = client.get("/api/settings").json()
            assert data["remote"]["secret_set"] is True
            assert "secret" not in data["remote"]

            updated = client.put(
                "/api/settings",
                json={"remote": {"include_library": False}},
                headers=auth_headers(),
            )
            assert updated.status_code == 200
    finally:
        app.state.ctx.remote.stop()

    saved = Settings.load_file(app_env["paths"])
    assert saved.remote.secret == secret
    assert saved.remote.include_library is False


def test_runtime_file_round_trip(app_env) -> None:
    paths: Paths = app_env["paths"]
    write_runtime_file(
        paths,
        {
            "state": "online",
            "url": f"{FAKE_URL}/mcp/abc",
            "port": 8102,
            "include_library": True,
        },
    )
    record = read_runtime_file(paths)
    assert record is not None
    assert record["url"] == f"{FAKE_URL}/mcp/abc"
    assert record["include_library"] is True
    assert record["pid"] == os.getpid()
    assert runtime_process_alive(record) is True

    paths.remote_runtime_file.unlink()
    assert read_runtime_file(paths) is None
    assert runtime_process_alive(None) is False
    assert runtime_process_alive({"pid": 0}) is False
