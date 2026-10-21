"""Public MCP address through a Cloudflare Quick Tunnel.

The tunnel connects a local, secret-protected MCP endpoint to an HTTPS
address that can be pasted into ChatGPT or Claude.ai. Nothing here opens a
tunnel unless the user asks for one, and the tunnel dies with the process.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import queue
import re
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from buscateses.config import (
    ConfigError,
    Paths,
    Settings,
    get_paths,
    get_settings,
    read_config_file,
)
from buscateses.downloads import DownloadError, download_file
from buscateses.jobs import JobCancelled, JobContext, JobError, JobManager
from buscateses.mcp_server import create_server
from buscateses.netutil import find_free_socket

logger = logging.getLogger(__name__)

STATE_OFF = "off"
STATE_DOWNLOADING = "downloading"
STATE_STARTING = "starting"
STATE_ONLINE = "online"
STATE_ERROR = "error"
ACTIVE_STATES = frozenset({STATE_DOWNLOADING, STATE_STARTING})
TUNNEL_TIMEOUT = 40.0
DOWNLOADS_START_PORT = 8102
_RUNTIME_START_TIMEOUT = 8.0
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
_UNSET = object()


class RemoteError(RuntimeError):
    """Raised when the public address cannot be created or stopped."""


@dataclass(frozen=True)
class CloudflaredArtifact:
    """A pinned cloudflared release file for one platform."""

    filename: str
    sha256: str
    size: int
    archive: bool = False


CLOUDFLARED_VERSION = "2026.10.3"
CLOUDFLARED_BASE_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/"
)
_ARTIFACTS: dict[tuple[str, str], CloudflaredArtifact] = {
    ("darwin", "arm64"): CloudflaredArtifact(
        "cloudflared-darwin-arm64.tgz",
        "1165d14b6f6360a3b8c283d7d1100024d6ec7cd9bc9e32519e96150645f3ec55",
        19841881,
        archive=True,
    ),
    ("darwin", "amd64"): CloudflaredArtifact(
        "cloudflared-darwin-amd64.tgz",
        "81b7b85f2376815c4f59b1aa323887b62e16ce8b67555c2e1577dc23f2a82000",
        21823395,
        archive=True,
    ),
    ("win32", "amd64"): CloudflaredArtifact(
        "cloudflared-windows-amd64.exe",
        "76b2805253d0d6ffacb092a7acfbee99e7af6917835c0c5410e23f3ef5052da2",
        55329336,
    ),
    ("linux", "amd64"): CloudflaredArtifact(
        "cloudflared-linux-amd64",
        "053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0",
        40227721,
    ),
}


def normalize_machine(machine: str) -> str:
    """Map ``uname -m`` spellings to ``arm64`` or ``amd64``."""
    value = str(machine or "").strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64", "x64"}:
        return "amd64"
    return value


def artifact_for(
    system: str | None = None,
    machine: str | None = None,
) -> CloudflaredArtifact:
    """Return the pinned cloudflared file for the current platform."""
    resolved_system = (system or sys.platform).lower()
    resolved_machine = normalize_machine(machine or platform.machine())
    artifact = _ARTIFACTS.get((resolved_system, resolved_machine))
    if artifact is None:
        raise RemoteError(
            "O endereço público não está disponível neste sistema. "
            "Use o BuscaTeses no macOS, no Windows ou no Linux x86_64."
        )
    return artifact


def cloudflared_path(paths: Paths) -> Path:
    """Return the destination of the cloudflared executable."""
    suffix = ".exe" if sys.platform == "win32" else ""
    return paths.bin_dir / f"cloudflared-{CLOUDFLARED_VERSION}{suffix}"


def ensure_cloudflared(
    paths: Paths,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> Path:
    """Download and verify cloudflared once, returning its local path.

    The executable is fetched from the official GitHub release and checked
    against a pinned SHA-256 before it is ever executed.
    """
    artifact = artifact_for(system, machine)
    destination = cloudflared_path(paths)
    if destination.exists() and _installed_matches(destination, artifact):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    download_target = destination
    if artifact.archive:
        download_target = destination.with_name(f"{artifact.filename}.download")
    try:
        download_file(
            CLOUDFLARED_BASE_URL + artifact.filename,
            download_target,
            expected_bytes=artifact.size,
            expected_sha256=artifact.sha256,
            progress=progress,
        )
        if artifact.archive:
            _extract_cloudflared(download_target, destination)
            _write_verified_marker(destination, artifact)
    except DownloadError as exc:
        raise RemoteError(
            "Não foi possível baixar o conector do Cloudflare. "
            "Verifique sua conexão e tente de novo."
        ) from exc
    finally:
        if artifact.archive and download_target.exists():
            download_target.unlink(missing_ok=True)

    if not destination.exists():
        raise RemoteError(
            "O conector do Cloudflare foi baixado, mas não pôde ser preparado. "
            "Tente de novo."
        )
    if sys.platform != "win32":
        destination.chmod(0o755)
    return destination


def verified_marker_path(destination: Path) -> Path:
    """Sidecar file recording which verified archive produced *destination*."""
    return destination.with_name(f"{destination.name}.verified.json")


def _write_verified_marker(destination: Path, artifact: CloudflaredArtifact) -> None:
    """Record the pinned archive digest and the extracted binary digest.

    macOS releases are ``.tgz`` archives: the pinned SHA-256 belongs to the
    archive, not to the extracted executable, so the binary's own digest is
    stored right after the verified extraction.
    """
    from buscateses.downloads import sha256_file

    payload = {
        "version": CLOUDFLARED_VERSION,
        "archive": artifact.filename,
        "archive_sha256": artifact.sha256.lower(),
        "binary_sha256": sha256_file(destination),
    }
    verified_marker_path(destination).write_text(json.dumps(payload), encoding="utf-8")


def _installed_matches(destination: Path, artifact: CloudflaredArtifact) -> bool:
    """Return whether the installed executable is the pinned, verified one."""
    if not artifact.archive:
        return _digest_matches(destination, artifact)
    try:
        record = json.loads(verified_marker_path(destination).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict):
        return False
    if str(record.get("archive_sha256", "")).lower() != artifact.sha256.lower():
        return False
    from buscateses.downloads import sha256_file

    try:
        return sha256_file(destination) == str(record.get("binary_sha256", "")).lower()
    except OSError:
        return False


def _digest_matches(path: Path, artifact: CloudflaredArtifact) -> bool:
    """Return whether an existing file matches the pinned size and hash."""
    try:
        if path.stat().st_size != artifact.size:
            return False
        from buscateses.downloads import sha256_file

        return sha256_file(path) == artifact.sha256.lower()
    except OSError:
        return False


def _extract_cloudflared(archive: Path, destination: Path) -> None:
    """Extract the single ``cloudflared`` member of a release tarball."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            member = next(
                (item for item in bundle.getmembers() if item.name.endswith("cloudflared")),
                None,
            )
            if member is None or not member.isfile():
                raise RemoteError(
                    "O arquivo do conector do Cloudflare não contém o programa esperado. "
                    "Tente de novo."
                )
            source = bundle.extractfile(member)
            if source is None:
                raise RemoteError("Não foi possível preparar o conector do Cloudflare.")
            with source, destination.open("wb") as handle:
                while chunk := source.read(1024 * 1024):
                    handle.write(chunk)
    except (OSError, tarfile.TarError) as exc:
        raise RemoteError(
            "O arquivo do conector do Cloudflare está corrompido. Tente de novo."
        ) from exc


class RemoteController:
    """Own the local MCP endpoint, the tunnel process and their state."""

    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        jobs: JobManager,
        *,
        embedder: Any = _UNSET,
        cloudflared_command: Sequence[str] | None = None,
        settings_saver: Callable[[Settings], None] | None = None,
        on_runtime: Callable[[dict[str, Any] | None], None] | None = None,
        tunnel_timeout: float = TUNNEL_TIMEOUT,
    ) -> None:
        self.paths = paths
        self.jobs = jobs
        self.settings = settings
        self._embedder = embedder
        self._command = (
            list(cloudflared_command) if cloudflared_command is not None else None
        )
        self._save_settings = settings_saver
        self._on_runtime = on_runtime
        self._tunnel_timeout = float(tunnel_timeout)
        self._lock = threading.RLock()
        self._state = STATE_OFF
        self._message = "Endereço público desligado."
        self._url: str | None = None
        self._include_library = bool(settings.remote.include_library)
        self._port: int | None = None
        self._process: subprocess.Popen[str] | None = None
        self._uv_server: Any = None
        self._uv_thread: threading.Thread | None = None
        self._job_id: str | None = None
        self._stop_event = threading.Event()
        self._start_done = threading.Event()
        self._start_done.set()
        atexit.register(self.stop)

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        """Return the public-address state exposed by the API."""
        with self._lock:
            return {
                "state": self._state,
                "url": self._url,
                "include_library": self._include_library,
                "message": self._message,
                "cloudflared_installed": self._cloudflared_ready(),
            }

    def update_settings(self, settings: Settings) -> None:
        """Keep the latest settings and library preference in memory."""
        with self._lock:
            self.settings = settings
            self._include_library = bool(settings.remote.include_library)

    @property
    def port(self) -> int | None:
        """Local port of the MCP endpoint (available after start)."""
        with self._lock:
            return self._port

    @property
    def secret(self) -> str:
        """Secret path segment guarding the public endpoint."""
        return self._ensure_secret()

    @property
    def process_id(self) -> int | None:
        """PID of the running cloudflared process, when there is one."""
        with self._lock:
            return self._process.pid if self._process is not None else None

    # ----------------------------------------------------------------- control

    def start(self, include_library: bool = False) -> dict[str, Any]:
        """Create the public address in the background and return its state."""
        with self._lock:
            if self._state in ACTIVE_STATES or self._state == STATE_ONLINE:
                raise RemoteError(
                    "O endereço público já está sendo criado ou está ligado."
                )
            self._include_library = bool(include_library)
            self._persist_preferences()
            self._stop_event.clear()
            self._start_done.clear()
            self._url = None
            self._state = (
                STATE_DOWNLOADING if not self._cloudflared_ready() else STATE_STARTING
            )
            self._message = (
                "Baixando o conector do Cloudflare (cerca de 30 MB)…"
                if self._state == STATE_DOWNLOADING
                else "Criando o endereço público…"
            )
            try:
                self._job_id = self.jobs.submit(
                    "remote",
                    self._run_start,
                    exclusive=True,
                    message=self._message,
                )
            except JobError as exc:
                self._state = STATE_ERROR
                self._message = str(exc)
                raise RemoteError(str(exc)) from exc
        return self.status()

    def stop(self) -> dict[str, Any]:
        """Stop the tunnel and the local MCP endpoint."""
        with self._lock:
            self._stop_event.set()
            job_id = self._job_id
            self._state = STATE_OFF
            self._message = "Endereço público desligado."
            self._url = None
            self._publish_runtime(None)
        if job_id:
            self.jobs.cancel(job_id)
        self._terminate_process()
        self._stop_uvicorn()
        self._start_done.wait(timeout=10.0)
        with self._lock:
            self._job_id = None
            self._port = None
        return self.status()

    def rotate(self) -> dict[str, Any]:
        """Replace the secret path, restarting the tunnel when it is online."""
        with self._lock:
            was_active = self._state in ACTIVE_STATES or self._state == STATE_ONLINE
            include_library = self._include_library
            if was_active:
                self.stop()
            secret = secrets.token_urlsafe(24)
            self.settings = replace(
                self.settings,
                remote=replace(self.settings.remote, secret=secret),
            )
            self._persist_preferences()
        if was_active:
            return self.start(include_library)
        return self.status()

    # -------------------------------------------------------------- job worker

    def _run_start(self, ctx: JobContext) -> dict[str, Any]:
        """Download (when needed), start uvicorn and wait for the tunnel URL."""
        try:
            ctx.update(stage="download", message="Preparando o conector do Cloudflare…")
            binary = self._resolve_command(ctx)
            ctx.check_cancelled()
            self._set_state(STATE_STARTING, "Criando o endereço público…")

            secret = self._ensure_secret()
            port = self._start_http_server(secret)
            self._wait_local_port(port)
            ctx.update(stage="tunnel", message="Abrindo o endereço público…", progress=None)
            url = self._start_tunnel(binary, port)
            public_url = f"{url}/mcp/{secret}"
            with self._lock:
                if self._stop_event.is_set():
                    raise JobCancelled("Tarefa cancelada.")
                self._url = public_url
                self._state = STATE_ONLINE
                self._message = (
                    "Endereço público ativo. Ele funciona enquanto o BuscaTeses estiver aberto."
                )
                self._publish_runtime(self.status())
            ctx.update(
                stage="online",
                message="Endereço público pronto.",
                progress=1.0,
            )
            return self.status()
        except JobCancelled:
            with self._lock:
                if self._state != STATE_ERROR:
                    self._state = STATE_OFF
                    self._message = "Endereço público desligado."
                    self._url = None
            self._teardown()
            raise
        except RemoteError as exc:
            with self._lock:
                if not self._stop_event.is_set():
                    self._state = STATE_ERROR
                    self._message = str(exc)
                    self._url = None
            self._teardown()
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            logger.exception("Falha ao criar o endereço público.")
            with self._lock:
                self._state = STATE_ERROR
                self._message = (
                    "Não foi possível criar o endereço público. Tente de novo."
                )
                self._url = None
            self._teardown()
            raise RemoteError(self._message) from exc
        finally:
            self._start_done.set()

    def _resolve_command(self, ctx: JobContext) -> list[str]:
        """Return the cloudflared command, downloading it on first use."""
        if self._command is not None:
            return self._command
        binary = cloudflared_path(self.paths)
        if not binary.exists() or not self._cloudflared_ready():
            ctx.update(
                stage="download",
                message="Baixando o conector do Cloudflare (cerca de 30 MB)…",
                progress=0.0,
            )

            def report(done: int, total: int | None) -> None:
                ctx.check_cancelled()
                progress = (done / total) if total else 0.0
                ctx.update(
                    stage="download",
                    message="Baixando o conector do Cloudflare (cerca de 30 MB)…",
                    progress=min(0.99, progress),
                    bytes_done=done,
                    bytes_total=total,
                )

            ensure_cloudflared(self.paths, progress=report)
        ctx.update(stage="start", message="Conector pronto. Criando o endereço público…")
        return [str(binary), "tunnel"]

    def _start_http_server(self, secret: str) -> int:
        """Start the secret-path MCP app on a free loopback port."""
        import uvicorn

        try:
            sock = find_free_socket(DOWNLOADS_START_PORT)
        except OSError as exc:
            raise RemoteError(
                "Não há portas livres para o endereço público. Feche outros "
                "programas e tente de novo."
            ) from exc
        port = int(sock.getsockname()[1])

        with self._lock:
            settings = self.settings
        embedder = self._embedder
        server = create_server(
            self.paths,
            settings,
            **({} if embedder is _UNSET else {"embedder": embedder}),
            include_library=self._include_library,
            streamable_http_path=f"/mcp/{secret}",
            stateless_http=True,
            json_response=True,
            enable_dns_rebinding_protection=False,
            remote=True,
        )
        app = server.streamable_http_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,
            lifespan="on",
        )
        uv_server = uvicorn.Server(config)
        thread = threading.Thread(
            target=uv_server.run,
            kwargs={"sockets": [sock]},
            name="buscateses-remote-mcp",
            daemon=True,
        )
        with self._lock:
            self._uv_server = uv_server
            self._uv_thread = thread
            self._port = port
        thread.start()
        return port

    def _wait_local_port(self, port: int, timeout: float = 10.0) -> None:
        """Wait until the local MCP endpoint accepts connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                raise JobCancelled("Tarefa cancelada.")
            thread = self._uv_thread
            if thread is not None and not thread.is_alive():
                raise RemoteError(
                    "Não foi possível iniciar o servidor local do endereço público."
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RemoteError(
            "O servidor local do endereço público demorou para responder. Tente de novo."
        )

    def _start_tunnel(self, command: list[str], port: int) -> str:
        """Run cloudflared and capture the public trycloudflare.com URL."""
        argv = [
            *command,
            "--url",
            f"http://127.0.0.1:{port}",
            "--no-autoupdate",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RemoteError(
                "Não foi possível iniciar o conector do Cloudflare. "
                "Baixe o endereço público novamente."
            ) from exc
        with self._lock:
            self._process = process
        url = self._read_tunnel_url(process)
        threading.Thread(
            target=self._monitor_tunnel,
            args=(process,),
            name="buscateses-remote-monitor",
            daemon=True,
        ).start()
        return url

    def _read_tunnel_url(self, process: subprocess.Popen[str]) -> str:
        """Read cloudflared output until the public URL appears or times out."""
        if process.stdout is None:
            raise RemoteError("O conector do Cloudflare não enviou nenhuma resposta.")

        lines: queue.Queue[str | None] = queue.Queue(maxsize=200)
        found = threading.Event()

        def pump() -> None:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip()
                if line:
                    logger.debug("cloudflared: %s", line)
                if found.is_set():
                    continue
                try:
                    lines.put_nowait(line)
                except queue.Full:
                    pass
            try:
                lines.put_nowait(None)
            except queue.Full:
                pass

        threading.Thread(
            target=pump, name="buscateses-cloudflared-output", daemon=True
        ).start()
        deadline = time.monotonic() + self._tunnel_timeout
        try:
            while True:
                if self._stop_event.is_set():
                    raise JobCancelled("Tarefa cancelada.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError(
                        "O Cloudflare não respondeu em 40 segundos. "
                        "Verifique sua conexão ou o firewall da rede e tente de novo."
                    )
                try:
                    line = lines.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    if process.poll() is not None:
                        raise RemoteError(
                            "O conector do Cloudflare foi encerrado antes de criar o "
                            "endereço. Tente de novo."
                        ) from None
                    continue
                if line is None:
                    raise RemoteError(
                        "O conector do Cloudflare foi encerrado antes de criar o "
                        "endereço. Tente de novo."
                    )
                match = _TUNNEL_URL.search(line)
                if match:
                    found.set()
                    return match.group(0).rstrip("/")
        finally:
            found.set()

    def _monitor_tunnel(self, process: subprocess.Popen[str]) -> None:
        """Mark the address as failed when cloudflared exits by itself."""
        returncode = process.wait()
        with self._lock:
            still_current = self._process is process
            unexpected = (
                still_current
                and not self._stop_event.is_set()
                and self._state == STATE_ONLINE
            )
            if unexpected:
                self._state = STATE_ERROR
                self._url = None
                self._message = (
                    "O endereço público foi encerrado pelo Cloudflare. "
                    "Crie um novo endereço para continuar."
                )
                self._publish_runtime(None)
        if unexpected:
            logger.warning("cloudflared exited unexpectedly with code %s.", returncode)
            self._stop_uvicorn()

    # ------------------------------------------------------------- shutdown

    def _teardown(self) -> None:
        """Stop the tunnel and the local server without changing final state."""
        self._terminate_process()
        self._stop_uvicorn()
        with self._lock:
            self._port = None
            self._publish_runtime(None)

    def _terminate_process(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Não foi possível encerrar o cloudflared.")

    def _stop_uvicorn(self) -> None:
        with self._lock:
            server = self._uv_server
            thread = self._uv_thread
            self._uv_server = None
            self._uv_thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    # --------------------------------------------------------------- helpers

    def _set_state(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def _ensure_secret(self) -> str:
        """Return the persisted secret, generating it on first use."""
        with self._lock:
            secret = self.settings.remote.secret
            if secret:
                return secret
            secret = secrets.token_urlsafe(24)
            self.settings = replace(
                self.settings,
                remote=replace(self.settings.remote, secret=secret),
            )
            self._persist_preferences()
            return secret

    def _persist_preferences(self) -> None:
        """Save the secret and library preference without exposing the secret."""
        if self._save_settings is None:
            return
        try:
            self._save_settings(self.settings)
        except Exception:  # noqa: BLE001 - the tunnel can still run in memory
            logger.warning("Não foi possível salvar as preferências do endereço público.")

    def _cloudflared_ready(self) -> bool:
        """Return whether cloudflared is available without downloading it."""
        if self._command is not None:
            return True
        try:
            artifact = artifact_for()
        except RemoteError:
            return False
        return _digest_matches(cloudflared_path(self.paths), artifact)

    def _publish_runtime(self, payload: dict[str, Any] | None) -> None:
        """Hand the runtime record to the CLI callback, when configured."""
        if self._on_runtime is None:
            return
        if payload is not None:
            payload = {**payload, "port": self._port}
        self._on_runtime(payload)


def save_remote_settings(paths: Paths, settings: Settings) -> None:
    """Persist only the ``remote`` section, preserving unrelated edits."""
    try:
        raw = read_config_file(paths)
    except ConfigError:
        raw = {}
    raw["remote"] = {
        "secret": settings.remote.secret,
        "include_library": settings.remote.include_library,
    }
    Settings.from_dict(raw, apply_env=False).save(paths)


def runtime_payload(status: dict[str, Any]) -> dict[str, Any]:
    """Build the small runtime record used by ``buscateses remote stop``."""
    return {
        "state": status.get("state"),
        "url": status.get("url"),
        "port": status.get("port"),
        "include_library": bool(status.get("include_library")),
        "pid": os.getpid(),
        "version": 1,
    }


def write_runtime_file(paths: Paths, status: dict[str, Any]) -> None:
    """Atomically write ``remote.json`` for a CLI-started tunnel."""
    paths.ensure()
    destination = paths.remote_runtime_file
    payload = runtime_payload(status)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_runtime_file(paths: Paths) -> dict[str, Any] | None:
    """Read ``remote.json`` if a CLI-started tunnel recorded one."""
    try:
        raw = json.loads(paths.remote_runtime_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _windows_process_alive(pid: int) -> bool:
    """Check a PID on Windows without ``os.kill``.

    On Windows ``os.kill(pid, 0)`` does not probe the process: signal 0 is
    ``CTRL_C_EVENT`` (sent to the whole console group) and other values call
    ``TerminateProcess``.
    """
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def runtime_process_alive(record: dict[str, Any] | None) -> bool:
    """Return whether the process recorded in ``remote.json`` is running."""
    if not record:
        return False
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def build_controller(
    paths: Paths | None = None,
    settings: Settings | None = None,
    jobs: JobManager | None = None,
    **kwargs: Any,
) -> RemoteController:
    """Convenience factory used by the CLI and tests."""
    resolved_paths = (paths or get_paths()).ensure()
    resolved_settings = settings or get_settings(resolved_paths)
    return RemoteController(
        resolved_paths,
        resolved_settings,
        jobs or JobManager(),
        **kwargs,
    )


__all__ = [
    "CLOUDFLARED_VERSION",
    "RemoteController",
    "RemoteError",
    "STATE_DOWNLOADING",
    "STATE_ERROR",
    "STATE_OFF",
    "STATE_ONLINE",
    "STATE_STARTING",
    "artifact_for",
    "build_controller",
    "cloudflared_path",
    "ensure_cloudflared",
    "read_runtime_file",
    "runtime_process_alive",
    "save_remote_settings",
    "write_runtime_file",
]
