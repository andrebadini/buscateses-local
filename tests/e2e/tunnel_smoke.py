#!/usr/bin/env python3
"""End-to-end check of the public MCP address through a real Cloudflare tunnel.

This is a **script**, not a pytest test: it needs internet access, downloads
the real ``cloudflared`` binary (verified by SHA-256) and opens a Quick Tunnel,
so it only runs on disposable CI machines. See
``.github/workflows/tunnel-e2e.yml``.

The script exits with status 0 only when every check passes. Logs are written
to ``TUNNEL_SMOKE_REPORT`` (default: ``tunnel-e2e-logs``) so the CI workflow
can upload them as an artifact.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from buscateses.catalog.installer import CatalogInstaller  # noqa: E402
from buscateses.config import Paths, Settings  # noqa: E402
from buscateses.downloads import sha256_file  # noqa: E402
from buscateses.jobs import JobManager  # noqa: E402
from buscateses.library.ingest import LibraryIngestor  # noqa: E402
from buscateses.library.store import LibraryStore  # noqa: E402
from buscateses.remote import (  # noqa: E402
    STATE_ERROR,
    STATE_ONLINE,
    RemoteController,
    artifact_for,
    cloudflared_path,
    save_remote_settings,
)

LOG = logging.getLogger("tunnel-smoke")
LOG_FILE_NAME = "tunnel-smoke.log"
REPORT_FILE_NAME = "report.txt"

#: Time allowed for the public URL to appear (download + cloudflared start).
TUNNEL_TIMEOUT = 300.0
#: Time allowed for a new tunnel to propagate through Cloudflare's edge.
PROPAGATION_TIMEOUT = 240.0
#: Time allowed for a killed tunnel to stop answering.
SHUTDOWN_TIMEOUT = 60.0
RPC_TIMEOUT = 30.0

RPC_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
#: Transient edge errors while a fresh Quick Tunnel propagates. A 404 can mean
#: Cloudflare has not finished wiring the hostname yet; the missing-secret
#: checks disable retries so a real 404 still fails immediately.
_RETRYABLE_STATUS = frozenset(
    {404, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530}
)
_CATALOG_TOOLS = {"search_theses", "get_thesis", "similar_theses", "catalog_status"}
_LIBRARY_TOOLS = {"search_library", "list_library_documents", "get_library_passage"}
_IDS = itertools.count(1)

LIBRARY_TEXT = (
    "Relatório sintético sobre educação inclusiva e acessibilidade. "
    "Este documento descreve práticas escolares, adaptações curriculares e "
    "tecnologias assistivas para estudantes com deficiência. "
) * 8


class SmokeFailure(RuntimeError):
    """A check failed; the message is shown in the final report."""


def check(condition: Any, message: str) -> None:
    """Raise :class:`SmokeFailure` when *condition* is false."""
    if not condition:
        raise SmokeFailure(message)
    LOG.info("OK: %s", message)


def _load_test_helpers() -> Any:
    """Import ``tests/conftest.py`` without importing pytest fixtures."""
    path = ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("buscateses_tunnel_conftest", path)
    if spec is None or spec.loader is None:
        raise SmokeFailure("Não foi possível carregar os dados sintéticos de teste.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- environment


def prepare_home(home: Path) -> tuple[Paths, Settings, Any, str]:
    """Create the synthetic catalog, embedder and one library document."""
    helpers = _load_test_helpers()
    os.environ["BUSCATESES_HOME"] = str(home)
    dataset = helpers.build_dataset(home / "dataset", count=24)
    paths = Paths.from_home(home).ensure()
    settings = Settings(dataset_url=str(dataset.root))
    LOG.info("Instalando o mini-catálogo sintético…")
    CatalogInstaller(paths, settings).install()
    check(paths.current_file.is_file(), "o mini-catálogo foi instalado")

    embedder = helpers.FakeEmbedder()
    store = LibraryStore(paths)
    ingestor = LibraryIngestor(paths, embedder, store=store)
    source = home / "documento-sintetico.txt"
    source.write_text(LIBRARY_TEXT, encoding="utf-8")
    outcome = ingestor.ingest_path(source)
    check(
        outcome.status == "ready",
        f"o documento sintético entrou na biblioteca ({outcome.message})",
    )
    check(store.counts().get("documents") == 1, "a biblioteca tem um documento")
    return paths, settings, embedder, outcome.doc_id


def _configure_logging(report_dir: Path) -> None:
    """Log to stdout and to a UTF-8 file uploaded by the CI workflow."""
    report_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(
        report_dir / LOG_FILE_NAME, mode="w", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(stream)
    root.addHandler(file_handler)
    # cloudflared output is useful when the tunnel fails; keep HTTP clients quiet.
    logging.getLogger("buscateses.remote").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# -------------------------------------------------------------------- tunnel


def wait_online(controller: RemoteController, timeout: float = TUNNEL_TIMEOUT) -> dict[str, Any]:
    """Poll the production controller until the public address is online."""
    deadline = time.monotonic() + timeout
    status = controller.status()
    while time.monotonic() < deadline:
        status = controller.status()
        if status["state"] == STATE_ONLINE:
            return status
        if status["state"] == STATE_ERROR:
            raise SmokeFailure(f"o endereço público falhou: {status['message']}")
        time.sleep(0.5)
    raise SmokeFailure(
        f"o endereço público não ficou pronto em {timeout:.0f}s "
        f"(estado {status['state']!r}: {status['message']})"
    )


def check_real_cloudflared(paths: Paths) -> None:
    """Confirm the downloaded binary matches the pinned release digest."""
    artifact = artifact_for()
    binary = cloudflared_path(paths)
    check(binary.is_file(), f"o cloudflared real foi baixado em {binary.name}")
    if artifact.archive:
        # macOS: the pinned digest is the .tgz's; the extraction records the
        # binary digest in a sidecar file that must match the executable.
        import json

        from buscateses.remote import verified_marker_path

        record = json.loads(verified_marker_path(binary).read_text(encoding="utf-8"))
        check(
            record.get("archive_sha256") == artifact.sha256.lower(),
            "o pacote .tgz baixado confere com a versão fixada",
        )
        check(
            sha256_file(binary) == record.get("binary_sha256"),
            "o executável extraído confere com o registro de verificação",
        )
        return
    check(
        binary.stat().st_size == artifact.size,
        f"o tamanho do cloudflared confere ({artifact.size} bytes)",
    )
    check(
        sha256_file(binary) == artifact.sha256.lower(),
        "o SHA-256 do cloudflared confere com a versão fixada",
    )


# ---------------------------------------------------------------------- MCP


def _rpc(
    client: httpx.Client,
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    notification: bool = False,
    retries: bool = True,
    timeout: float = RPC_TIMEOUT,
) -> httpx.Response:
    """POST one JSON-RPC message, retrying while Cloudflare propagates."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if not notification:
        body["id"] = next(_IDS)
    if params is not None:
        body["params"] = params

    deadline = time.monotonic() + (PROPAGATION_TIMEOUT if retries else 0.0)
    attempt = 0
    last_error = ""
    while True:
        attempt += 1
        try:
            response = client.post(url, json=body, headers=RPC_HEADERS, timeout=timeout)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if not retries or response.status_code not in _RETRYABLE_STATUS:
                return response
            last_error = f"HTTP {response.status_code}"
        if time.monotonic() >= deadline:
            raise SmokeFailure(
                f"a URL pública não respondeu a {method} ({last_error})"
            )
        if attempt == 1 or attempt % 10 == 0:
            LOG.info("Aguardando a URL pública propagar (%s)…", last_error)
        if "nodename nor servname" in last_error or "Name or service not known" in last_error:
            # The first lookup can happen before Cloudflare publishes the
            # quick-tunnel hostname; macOS then caches the negative answer.
            _flush_dns_cache()
        time.sleep(min(3.0, 0.5 * attempt))


def _flush_dns_cache() -> None:
    """Drop cached negative DNS answers on the CI runner (best effort)."""
    if sys.platform != "darwin":
        return
    for command in (["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]):
        subprocess.run(["sudo", "-n", *command], check=False, capture_output=True)


def _require_json(response: httpx.Response, label: str) -> dict[str, Any]:
    """Validate a JSON-RPC response and return its ``result`` object."""
    check(
        response.status_code == 200,
        f"{label} respondeu HTTP {response.status_code}",
    )
    content_type = response.headers.get("content-type", "").lower()
    check(
        content_type.startswith("application/json"),
        f"{label} devolveu application/json, sem SSE (content-type {content_type!r})",
    )
    payload = response.json()
    check("error" not in payload, f"{label} não devolveu erro JSON-RPC: {payload.get('error')}")
    result = payload.get("result")
    check(isinstance(result, dict), f"{label} devolveu um objeto 'result'")
    return result


def initialize_session(client: httpx.Client, url: str, label: str) -> None:
    """Run the MCP initialize handshake and the initialized notification."""
    payload = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "buscateses-tunnel-smoke", "version": "1.0"},
    }
    result = _require_json(_rpc(client, url, "initialize", payload), f"{label} initialize")
    server_info = result.get("serverInfo") or {}
    check(
        server_info.get("name") == "BuscaTeses",
        f"{label} initialize identificou o servidor BuscaTeses",
    )
    notification = _rpc(
        client, url, "notifications/initialized", notification=True
    )
    check(
        notification.status_code in {200, 202, 204},
        f"{label} notifications/initialized foi aceita "
        f"(HTTP {notification.status_code})",
    )


def list_tools(client: httpx.Client, url: str, label: str) -> set[str]:
    """Return the tool names advertised by the public endpoint."""
    result = _require_json(_rpc(client, url, "tools/list", {}), f"{label} tools/list")
    tools = result.get("tools")
    check(isinstance(tools, list), f"{label} tools/list devolveu uma lista")
    return {str(tool.get("name")) for tool in tools if isinstance(tool, dict)}


def call_tool(
    client: httpx.Client,
    url: str,
    name: str,
    arguments: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Call an MCP tool and return its structured payload."""
    result = _require_json(
        _rpc(client, url, "tools/call", {"name": name, "arguments": arguments}),
        f"{label} tools/call {name}",
    )
    check(not result.get("isError"), f"{label} {name} não devolveu erro de ferramenta")
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        content = result.get("content") or []
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        check(texts, f"{label} {name} devolveu conteúdo")
        payload = json.loads(str(texts[0]))
    check(isinstance(payload, dict), f"{label} {name} devolveu um objeto")
    return payload


def check_catalog_tools(names: set[str], label: str) -> None:
    """Assert the catalog tools are present and the library tools are hidden."""
    check(_CATALOG_TOOLS <= names, f"{label} lista as ferramentas do catálogo")
    hidden = _LIBRARY_TOOLS & names
    check(not hidden, f"{label} não expõe ferramentas da biblioteca ({sorted(hidden)})")


def check_library_tools(names: set[str], label: str) -> None:
    """Assert the opt-in public address also exposes the library tools."""
    check(_CATALOG_TOOLS <= names, f"{label} mantém as ferramentas do catálogo")
    check(_LIBRARY_TOOLS <= names, f"{label} expõe as ferramentas da biblioteca")


def tunnel_root(public_url: str) -> str:
    """Strip the secret path from a public MCP URL."""
    return public_url.split("/mcp/", 1)[0]


def secret_from(public_url: str) -> str:
    """Return the secret path segment of a public MCP URL."""
    return public_url.rstrip("/").rsplit("/", 1)[-1]


def check_missing_secret(client: httpx.Client, public_url: str) -> None:
    """GET and POST the tunnel URL without the secret; both must be 404."""
    root = tunnel_root(public_url)
    payload = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "buscateses-tunnel-smoke", "version": "1.0"},
    }
    for path in (root, f"{root}/mcp"):
        get_response = client.get(path, timeout=15.0)
        check(
            get_response.status_code == 404,
            f"GET em {path.removeprefix(root) or '/'} sem o segredo devolve 404 "
            f"(HTTP {get_response.status_code})",
        )
        post_response = _rpc(client, path, "initialize", payload, retries=False)
        check(
            post_response.status_code == 404,
            f"POST em {path.removeprefix(root) or '/'} sem o segredo devolve 404 "
            f"(HTTP {post_response.status_code})",
        )


def wait_url_gone(client: httpx.Client, url: str, timeout: float = SHUTDOWN_TIMEOUT) -> None:
    """Wait until a closed tunnel URL stops answering successfully."""
    deadline = time.monotonic() + timeout
    last = "sem resposta"
    while time.monotonic() < deadline:
        try:
            response = client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": next(_IDS),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "buscateses-tunnel-smoke", "version": "1.0"},
                    },
                },
                headers=RPC_HEADERS,
                timeout=15.0,
            )
        except httpx.TransportError:
            return
        if response.status_code != 200:
            return
        last = f"HTTP {response.status_code}"
        time.sleep(1.0)
    raise SmokeFailure(f"a URL antiga continuou respondendo depois da rotação ({last})")


# ----------------------------------------------------------------- lifecycle


def _process_alive(pid: int | None) -> bool:
    """Return whether *pid* is still running, on POSIX and Windows."""
    if not pid:
        return False
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return re.search(rf"\b{pid}\b", completed.stdout) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_process_gone(pid: int | None, timeout: float = 15.0) -> None:
    """Wait for cloudflared to exit and fail when it is still alive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.2)
    raise SmokeFailure(f"o processo do cloudflared (PID {pid}) continua ativo")


def _port_open(port: int | None) -> bool:
    """Return whether a local TCP port still accepts connections."""
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def stop_and_check(controller: RemoteController, label: str) -> None:
    """Stop the address and confirm cloudflared and the local server are gone."""
    pid = controller.process_id
    port = controller.port
    controller.stop()
    status = controller.status()
    check(status["state"] == "off", f"{label}: o estado voltou a 'off'")
    check(status["url"] is None, f"{label}: a URL pública foi removida")
    check(controller.process_id is None, f"{label}: o controlador soltou o processo")
    _wait_process_gone(pid)
    check(not _process_alive(pid), f"{label}: o processo do cloudflared encerrou")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _port_open(port):
        time.sleep(0.2)
    check(not _port_open(port), f"{label}: o servidor MCP local foi encerrado")


# --------------------------------------------------------------------- flows


def run_catalog_phase(
    client: httpx.Client,
    controller: RemoteController,
    paths: Paths,
) -> None:
    """Start without the library, verify the catalog API and rotate the secret."""
    controller.start(include_library=False)
    status = wait_online(controller)
    public_url = str(status["url"])
    check(
        public_url.startswith("https://") and "/mcp/" in public_url,
        f"a URL pública foi criada ({public_url.rsplit('/', 1)[0]}/…)",
    )
    check(status["include_library"] is False, "a biblioteca não foi incluída")
    check_real_cloudflared(paths)

    initialize_session(client, public_url, "catálogo")
    names = list_tools(client, public_url, "catálogo")
    check_catalog_tools(names, "catálogo")

    payload = call_tool(
        client,
        public_url,
        "search_theses",
        {"query": "educação inclusiva", "limit": 3},
        "catálogo",
    )
    results = payload.get("results") or []
    check(int(payload.get("total") or 0) >= 1, "search_theses encontrou resultados")
    check(bool(results), "search_theses devolveu a lista de resultados")
    check(
        any("inclusiva" in str(item.get("title") or "").lower() for item in results),
        "search_theses devolveu um trabalho sobre educação inclusiva",
    )
    check_missing_secret(client, public_url)

    old_url = public_url
    old_secret = secret_from(old_url)
    rotated = controller.rotate()
    check(
        rotated["state"] in {"downloading", "starting", "online"},
        "rotate aceitou criar um novo endereço",
    )
    new_status = wait_online(controller)
    new_url = str(new_status["url"])
    check(new_url != old_url, "rotate criou uma URL pública diferente")
    check(secret_from(new_url) != old_secret, "rotate trocou o segredo do caminho")

    initialize_session(client, new_url, "após rotação")
    new_names = list_tools(client, new_url, "após rotação")
    check_catalog_tools(new_names, "após rotação")
    old_secret_on_new = f"{tunnel_root(new_url)}/mcp/{old_secret}"
    stale = _rpc(
        client,
        old_secret_on_new,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "buscateses-tunnel-smoke", "version": "1.0"},
        },
        retries=False,
    )
    check(
        stale.status_code == 404,
        f"o segredo antigo devolve 404 na URL nova (HTTP {stale.status_code})",
    )
    wait_url_gone(client, old_url)
    LOG.info("OK: a URL antiga deixou de funcionar após a rotação")
    stop_and_check(controller, "após a rotação")


def run_library_phase(
    client: httpx.Client,
    controller: RemoteController,
) -> None:
    """Start with ``include_library=True`` and verify the library tools."""
    controller.start(include_library=True)
    status = wait_online(controller)
    public_url = str(status["url"])
    check(status["include_library"] is True, "a biblioteca foi incluída")

    initialize_session(client, public_url, "biblioteca")
    names = list_tools(client, public_url, "biblioteca")
    check_library_tools(names, "biblioteca")

    documents = call_tool(
        client, public_url, "list_library_documents", {}, "biblioteca"
    )
    check(
        len(documents.get("documents") or []) == 1,
        "list_library_documents encontrou o documento sintético",
    )
    payload = call_tool(
        client,
        public_url,
        "search_library",
        {"query": "acessibilidade", "limit": 5},
        "biblioteca",
    )
    results = payload.get("results") or []
    check(int(payload.get("total") or 0) >= 1, "search_library encontrou resultados")
    check(
        any(
            "documento-sintetico" in str(item.get("filename") or "")
            for item in results
        ),
        "search_library devolveu o documento sintético",
    )
    stop_and_check(controller, "biblioteca")


def run() -> None:
    """Execute every tunnel check, raising :class:`SmokeFailure` on errors."""
    home = Path(tempfile.mkdtemp(prefix="buscateses-tunnel-smoke-"))
    LOG.info("Pasta de dados temporária: %s", home)
    paths, settings, embedder, _doc_id = prepare_home(home)
    controller = RemoteController(
        paths,
        settings,
        JobManager(),
        embedder=embedder,
        settings_saver=lambda new: save_remote_settings(paths, new),
    )
    client = httpx.Client(follow_redirects=False, trust_env=False)
    try:
        run_catalog_phase(client, controller, paths)
        run_library_phase(client, controller)
    finally:
        try:
            controller.stop()
        except Exception:  # noqa: BLE001 - never hide the original failure
            LOG.warning("Falha ao desligar o endereço no encerramento.", exc_info=True)
        client.close()


def main() -> int:
    """Run the smoke test and return the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    report_dir = Path(os.environ.get("TUNNEL_SMOKE_REPORT", "tunnel-e2e-logs"))
    _configure_logging(report_dir)
    LOG.info(
        "Teste do endereço público: Python %s em %s",
        sys.version.split()[0],
        sys.platform,
    )
    started = time.time()
    outcome = "SUCESSO"
    failure = ""
    try:
        run()
    except SmokeFailure as exc:
        outcome = "FALHA"
        failure = str(exc)
        LOG.error("FALHA: %s", exc)
    except Exception as exc:  # noqa: BLE001 - the report needs the full traceback
        outcome = "FALHA"
        failure = f"{type(exc).__name__}: {exc}"
        LOG.error("FALHA inesperada: %s", failure)
        LOG.error("%s", traceback.format_exc())
    finally:
        elapsed = time.time() - started
        LOG.info("RESULTADO: %s (%.1fs)", outcome, elapsed)
        (report_dir / REPORT_FILE_NAME).write_text(
            "\n".join(
                [
                    "Teste do endereço público do BuscaTeses",
                    f"Resultado: {outcome}",
                    f"Plataforma: {sys.platform} / Python {sys.version.split()[0]}",
                    f"Duração: {elapsed:.1f}s",
                    f"Falha: {failure or 'nenhuma'}",
                    f"Log completo: {LOG_FILE_NAME}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return 0 if outcome == "SUCESSO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
