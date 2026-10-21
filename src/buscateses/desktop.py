"""Native desktop window and local server lifecycle (SPEC §11)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import replace
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from buscateses import __version__
from buscateses.config import Paths, Settings, get_paths, get_settings
from buscateses.netutil import find_free_port, find_free_socket

WINDOW_TITLE = "BuscaTeses"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 840
WINDOW_MIN_SIZE = (960, 640)
STATUS_TIMEOUT = 20.0
_log = logging.getLogger(__name__)


class DesktopError(RuntimeError):
    """Raised when the desktop app cannot start."""


def configure_logging(paths: Paths) -> None:
    """Write rotating logs under ``paths.logs`` (never stdout)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        return
    paths.ensure()
    handler = RotatingFileHandler(
        paths.log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)


def acquire_app_lock(paths: Paths) -> FileLock:
    """Take the cross-process single-instance lock, or raise a clear error."""
    paths.ensure()
    lock = FileLock(str(paths.app_lock), timeout=0)
    try:
        lock.acquire()
    except FileLockTimeout as exc:
        raise DesktopError(
            "O BuscaTeses já está aberto em outro processo. Feche-o e tente novamente."
        ) from exc
    return lock


def read_runtime(paths: Paths) -> dict[str, Any] | None:
    """Read and validate ``runtime.json``."""
    try:
        data = json.loads(paths.runtime_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data["port"])
        pid = int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= port <= 65535 or pid <= 0:
        return None
    return {
        "pid": pid,
        "port": port,
        "version": str(data.get("version") or ""),
        "started_at": data.get("started_at"),
    }


def detect_running_instance(paths: Paths, *, timeout: float = 0.7) -> dict[str, Any] | None:
    """Return the runtime info when our server answers on the recorded port."""
    runtime = read_runtime(paths)
    if runtime is None:
        return None
    status = _fetch_status(runtime["port"], timeout=timeout)
    if status is None:
        return None
    application = status.get("app") if isinstance(status, dict) else None
    if not isinstance(application, dict):
        return None
    version = str(application.get("version") or "")
    if runtime["version"] and version and runtime["version"] != version:
        return None
    reported_pid = application.get("pid")
    if reported_pid is not None:
        try:
            if int(reported_pid) != runtime["pid"]:
                return None
        except (TypeError, ValueError):
            return None
    return runtime


def open_folder(path: Path) -> None:
    """Open ``path`` in the operating system's file manager."""
    target = str(Path(path))
    if sys.platform == "darwin":
        command = ["open", target]
    elif sys.platform.startswith("win"):
        command = ["explorer", target]
    else:
        command = ["xdg-open", target]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_app(
    paths: Paths | None = None,
    settings: Settings | None = None,
    *,
    use_window: bool = True,
    open_browser: bool = True,
    stop_event: threading.Event | None = None,
) -> int:
    """Start the local server and open the desktop window (or the browser)."""
    import uvicorn

    from buscateses.api import create_app

    resolved_paths = (paths or get_paths()).ensure()
    resolved_settings = settings or get_settings(resolved_paths)
    configure_logging(resolved_paths)

    existing = detect_running_instance(resolved_paths)
    if existing is not None:
        url = f"http://127.0.0.1:{existing['port']}/"
        print(f"O BuscaTeses já está aberto em {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    lock = acquire_app_lock(resolved_paths)
    sock = None
    try:
        try:
            sock = find_free_socket(resolved_settings.server.port)
        except OSError as exc:
            raise DesktopError(str(exc)) from exc
        port = int(sock.getsockname()[1])
        resolved_settings = replace(
            resolved_settings, server=replace(resolved_settings.server, port=port)
        )
        quit_event = stop_event or threading.Event()
        holder: dict[str, Any] = {}

        def request_quit() -> None:
            quit_event.set()
            server = holder.get("server")
            if server is not None:
                server.should_exit = True

        app = create_app(resolved_paths, resolved_settings, quit_callback=request_quit)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        holder["server"] = server
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [sock]},
            name="buscateses-desktop-server",
            daemon=True,
        )
        thread.start()
        url = f"http://127.0.0.1:{port}/"
        try:
            if not _wait_for_status(port, timeout=STATUS_TIMEOUT):
                raise DesktopError("O servidor local não respondeu. Tente novamente.")
            _write_runtime(resolved_paths, port)
            if use_window:
                try:
                    _show_window(url, resolved_paths)
                except Exception as exc:  # noqa: BLE001 - graceful browser fallback
                    _log.warning("Janela nativa indisponível: %s", exc)
                    if open_browser:
                        webbrowser.open(url)
                    _wait_for_quit(quit_event)
            else:
                if open_browser:
                    webbrowser.open(url)
                _wait_for_quit(quit_event)
        finally:
            try:
                app.state.ctx.remote.stop()
            except Exception:  # noqa: BLE001 - shutdown must always proceed
                _log.warning("Falha ao desligar o endereço público.", exc_info=True)
            server.should_exit = True
            thread.join(timeout=5.0)
            _remove_runtime(resolved_paths)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        lock.release()
    return 0


def _show_window(url: str, paths: Paths) -> None:
    import webview

    storage_path = paths.home / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    webview.create_window(
        WINDOW_TITLE,
        url=url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        text_select=True,
    )
    webview.start(
        private_mode=False,
        storage_path=str(storage_path),
    )


def _wait_for_quit(quit_event: threading.Event) -> None:
    while not quit_event.wait(0.4):
        pass


def _wait_for_status(port: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _fetch_status(port, timeout=0.5) is not None:
            return True
        time.sleep(0.1)
    return False


def _fetch_status(port: int, *, timeout: float) -> dict[str, Any] | None:
    url = f"http://127.0.0.1:{int(port)}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=max(0.1, timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_runtime(paths: Paths, port: int) -> None:
    payload = {
        "pid": os.getpid(),
        "port": int(port),
        "version": __version__,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    destination = paths.runtime_file
    descriptor, temporary = tempfile.mkstemp(
        prefix=".runtime.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _remove_runtime(paths: Paths) -> None:
    runtime = read_runtime(paths)
    if runtime is not None and runtime["pid"] != os.getpid():
        return
    try:
        paths.runtime_file.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "DesktopError",
    "WINDOW_MIN_SIZE",
    "acquire_app_lock",
    "configure_logging",
    "detect_running_instance",
    "find_free_port",
    "open_folder",
    "read_runtime",
    "run_app",
]
