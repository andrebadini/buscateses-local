"""Tests for desktop port choice, single-instance detection and lifecycle."""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from buscateses import desktop
from buscateses.desktop import (
    DesktopError,
    acquire_app_lock,
    detect_running_instance,
    find_free_port,
    open_folder,
    read_runtime,
    run_app,
)


def test_find_free_port_skips_occupied_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        busy = int(occupied.getsockname()[1])
        chosen = find_free_port(busy, attempts=5)
        assert chosen != busy
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
            check.bind(("127.0.0.1", chosen))


def test_find_free_port_clamps_invalid_start() -> None:
    port = find_free_port(99999, attempts=2)
    assert 1 <= port <= 65535


def test_read_runtime_validates_payload(app_env) -> None:
    paths = app_env["paths"].ensure()
    assert read_runtime(paths) is None
    paths.runtime_file.write_text("não é json", encoding="utf-8")
    assert read_runtime(paths) is None
    paths.runtime_file.write_text(json.dumps({"port": 0, "pid": 1}), encoding="utf-8")
    assert read_runtime(paths) is None
    paths.runtime_file.write_text(
        json.dumps({"port": 8123, "pid": 99, "version": "1.0.0"}), encoding="utf-8"
    )
    runtime = read_runtime(paths)
    assert runtime == {
        "pid": 99,
        "port": 8123,
        "version": "1.0.0",
        "started_at": None,
    }


def test_detect_running_instance_checks_status(app_env, monkeypatch) -> None:
    paths = app_env["paths"].ensure()
    paths.runtime_file.write_text(
        json.dumps({"port": 8123, "pid": 4242, "version": "1.0.0"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        desktop,
        "_fetch_status",
        lambda port, timeout: {"app": {"version": "1.0.0", "pid": 4242}},
    )
    assert detect_running_instance(paths)["port"] == 8123

    monkeypatch.setattr(
        desktop,
        "_fetch_status",
        lambda port, timeout: {"app": {"version": "9.9.9", "pid": 4242}},
    )
    assert detect_running_instance(paths) is None

    monkeypatch.setattr(
        desktop,
        "_fetch_status",
        lambda port, timeout: {"app": {"version": "1.0.0", "pid": 1}},
    )
    assert detect_running_instance(paths) is None

    monkeypatch.setattr(desktop, "_fetch_status", lambda port, timeout: None)
    assert detect_running_instance(paths) is None


def test_run_app_without_window_starts_and_stops(app_env) -> None:
    paths = app_env["paths"]
    port = find_free_port(49152)
    settings = replace(
        app_env["settings"],
        server=replace(app_env["settings"].server, port=port),
        updates=replace(app_env["settings"].updates, check_app=False),
    )
    stop = threading.Event()
    stop.set()
    assert run_app(
        paths,
        settings,
        use_window=False,
        open_browser=False,
        stop_event=stop,
    ) == 0
    assert not paths.runtime_file.exists()


def test_app_lock_blocks_a_second_process(app_env) -> None:
    paths = app_env["paths"]
    first = acquire_app_lock(paths)
    try:
        with pytest.raises(DesktopError, match="já está aberto"):
            acquire_app_lock(paths)
    finally:
        first.release()
    second = acquire_app_lock(paths)
    second.release()


def test_run_app_passes_a_prebound_socket_to_uvicorn(app_env, monkeypatch) -> None:
    import uvicorn

    paths = app_env["paths"]
    settings = replace(
        app_env["settings"],
        updates=replace(app_env["settings"].updates, check_app=False),
    )
    stop = threading.Event()
    captured: dict[str, int] = {}

    def fake_run(self, sockets=None):
        captured["port"] = int(sockets[0].getsockname()[1])
        stop.set()

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(desktop, "_wait_for_status", lambda port, timeout: True)

    assert run_app(
        paths,
        settings,
        use_window=False,
        open_browser=False,
        stop_event=stop,
    ) == 0
    assert captured["port"] > 0
    # The lock is released and the socket closed when the server stops.
    lock = acquire_app_lock(paths)
    lock.release()


def test_run_app_falls_back_to_browser(app_env, monkeypatch) -> None:
    paths = app_env["paths"]
    port = find_free_port(49152)
    settings = replace(
        app_env["settings"],
        server=replace(app_env["settings"].server, port=port),
        updates=replace(app_env["settings"].updates, check_app=False),
    )
    stop = threading.Event()
    stop.set()
    opened: list[str] = []

    def broken_window(url: str, paths_arg) -> None:
        raise RuntimeError("sem interface gráfica")

    monkeypatch.setattr(desktop, "_show_window", broken_window)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))
    assert run_app(paths, settings, stop_event=stop) == 0
    assert opened and opened[0].startswith("http://127.0.0.1:")


def test_run_app_reuses_running_instance(app_env, monkeypatch) -> None:
    paths = app_env["paths"].ensure()
    paths.runtime_file.write_text(
        json.dumps({"port": 8123, "pid": 1, "version": "1.0.0"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        desktop,
        "detect_running_instance",
        lambda paths_arg: {"port": 8123, "pid": 1, "version": "1.0.0"},
    )
    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))
    assert run_app(paths, app_env["settings"], open_browser=True) == 0
    assert opened == ["http://127.0.0.1:8123/"]
    assert paths.runtime_file.exists()


def test_show_window_configures_pywebview(app_env, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    calls: dict[str, dict] = {}

    def create_window(title, **kwargs):
        calls["window"] = {"title": title, **kwargs}

    def start(**kwargs):
        calls["start"] = kwargs

    monkeypatch.setitem(
        sys.modules, "webview", SimpleNamespace(create_window=create_window, start=start)
    )
    paths = app_env["paths"].ensure()
    desktop._show_window("http://127.0.0.1:8101/", paths)
    assert calls["window"]["title"] == "BuscaTeses"
    assert calls["window"]["width"] == 1280
    assert calls["window"]["height"] == 840
    assert calls["window"]["min_size"] == (960, 640)
    assert calls["window"]["text_select"] is True
    assert calls["start"]["private_mode"] is False
    assert calls["start"]["storage_path"] == str(paths.home / "webview")
    assert (paths.home / "webview").is_dir()


def test_open_folder_uses_platform_command(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        calls.append(command)

    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)
    open_folder(tmp_path)
    assert calls
    if desktop.sys.platform == "darwin":
        assert calls[0][0] == "open"
    elif desktop.sys.platform.startswith("win"):
        assert calls[0][0] == "explorer"
    else:
        assert calls[0][0] == "xdg-open"
    assert calls[0][1] == str(tmp_path)
