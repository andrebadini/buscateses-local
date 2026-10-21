"""Command-line interface (SPEC §11)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from buscateses import __version__
from buscateses.catalog.installer import CatalogError, CatalogInstaller
from buscateses.catalog.search import CatalogSearch, SearchError, SearchQuery
from buscateses.config import ConfigError, Paths, Settings, get_paths, get_settings
from buscateses.downloads import DownloadError
from buscateses.embeddings import build_embedder
from buscateses.embeddings.local_onnx import download_model
from buscateses.jobs import JobManager
from buscateses.library.extract import ExtractionError
from buscateses.library.ingest import LibraryIngestor, reindex_missing
from buscateses.library.store import LibraryError, LibraryStore

CommandBuilder = Callable[[argparse.ArgumentParser], None]
CommandHandler = Callable[[argparse.Namespace], int]

COMMAND_BUILDERS: dict[str, CommandBuilder] = {}
COMMAND_HELP: dict[str, str] = {}
COMMAND_HANDLERS: dict[str, CommandHandler] = {}


def register_command(
    name: str,
    help_text: str,
    builder: CommandBuilder,
    handler: CommandHandler,
) -> None:
    """Register a CLI subcommand; future modules can extend the parser here."""
    COMMAND_BUILDERS[name] = builder
    COMMAND_HELP[name] = help_text
    COMMAND_HANDLERS[name] = handler


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``buscateses`` console script."""
    _ensure_streams()
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not getattr(arguments, "command", None):
        # Opening the app is the default action for both source and packaged
        # installations; ``--help`` still prints the command list.
        arguments.command = "app"
    command = arguments.command
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(arguments))
    except KeyboardInterrupt:
        print("\nOperação cancelada.", file=sys.stderr)
        return 130
    except (ConfigError, CatalogError, SearchError, DownloadError, LibraryError,
            ExtractionError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - desktop/MCP integration errors
        message = str(exc).strip() or "Ocorreu um erro inesperado."
        print(f"Erro: {message}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the extensible argparse tree."""
    parser = argparse.ArgumentParser(
        prog="buscateses",
        description="Busca local no Catálogo de Teses e Dissertações da CAPES.",
    )
    parser.add_argument("--version", action="version", version=f"BuscaTeses {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="comando")
    help_texts = dict(COMMAND_HELP)
    help_texts.update(_BUILTIN_HELP)
    for name, builder in {**_BUILTIN_BUILDERS, **COMMAND_BUILDERS}.items():
        subparser = subparsers.add_parser(name, help=help_texts.get(name, ""))
        builder(subparser)
    return parser


def _build_app(parser: argparse.ArgumentParser) -> None:
    del parser


def _build_serve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1", help="Endereço local (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Porta HTTP (padrão: 8101)")
    parser.add_argument("--no-browser", action="store_true", help="Não abrir o navegador")


def _build_setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", action="store_true", help="Não pedir confirmação")
    parser.add_argument(
        "--only",
        action="append",
        choices=["model", "catalog"],
        metavar="model|catalog",
        help="Instalar somente um componente (pode repetir)",
    )


def _build_update(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", action="store_true", help="Não pedir confirmação")


def _build_status(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", help="Saída JSON")


def _build_search(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("consulta", help="Texto da busca")
    parser.add_argument("--limit", type=int, default=10, help="Número de resultados (padrão: 10)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Saída JSON")
    parser.add_argument("--mode", choices=["hybrid", "text", "semantic"], default="hybrid")
    parser.add_argument("--sort", choices=["relevance", "year_desc", "year_asc"], default="relevance")


def _build_library(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="library_command", metavar="ação")
    add = subparsers.add_parser("add", help="Adiciona arquivos à biblioteca")
    add.add_argument("arquivos", nargs="+", help="Arquivos PDF, EPUB, DOCX, TXT, MD ou HTML")
    listing = subparsers.add_parser("list", help="Lista os documentos da biblioteca")
    listing.add_argument("--json", action="store_true", dest="as_json", help="Saída JSON")
    remove = subparsers.add_parser("remove", help="Remove um documento da biblioteca")
    remove.add_argument("id", help="Identificador do documento")
    remove.add_argument("--yes", action="store_true", help="Não pedir confirmação")
    reindex = subparsers.add_parser("reindex", help="Gera vetores que faltam")
    reindex.add_argument("--yes", action="store_true", help="Não pedir confirmação")


def _build_mcp(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--http", action="store_true", help="Usar Streamable HTTP")
    parser.add_argument("--port", type=int, default=None, help="Porta HTTP do MCP (padrão: 8102)")
    parser.add_argument("--token", default=None, help="Token Bearer explícito")
    parser.add_argument(
        "--allow-no-token",
        action="store_true",
        help="Permitir HTTP sem token (apenas em 127.0.0.1)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        metavar="HOST",
        help=(
            "Host público adicional aceito pelo servidor HTTP (ex.: "
            "minha-maquina.tailnet.ts.net; pode repetir)"
        ),
    )


def _build_remote(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="acao", required=True)
    start = actions.add_parser(
        "start", help="Cria um endereço público para ChatGPT e Claude.ai"
    )
    start.add_argument(
        "--include-library",
        action="store_true",
        help="Permitir que o assistente pesquise a biblioteca pessoal",
    )
    actions.add_parser("stop", help="Desliga o endereço público")
    actions.add_parser("status", help="Mostra o estado do endereço público")


def _build_connect(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("cliente", nargs="?", help="Cliente de IA (ex.: claude_desktop)")
    parser.add_argument("--list", action="store_true", dest="list_clients", help="Listar clientes")


def _build_disconnect(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("cliente", help="Cliente de IA (ex.: claude_desktop)")


def _build_version(parser: argparse.ArgumentParser) -> None:
    del parser


def _app(args: argparse.Namespace) -> int:
    del args
    from buscateses.desktop import run_app

    return run_app()


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from buscateses.api import create_app
    from buscateses.desktop import DesktopError, acquire_app_lock
    from buscateses.netutil import bind_socket, try_bind

    host = str(args.host).strip()
    if host not in {"127.0.0.1", "localhost"}:
        print(
            "Erro: o servidor só pode escutar em 127.0.0.1 ou localhost.",
            file=sys.stderr,
        )
        return 2
    paths = get_paths().ensure()
    settings = get_settings(paths)
    requested = int(args.port) if args.port is not None else settings.server.port
    if not 0 <= requested <= 65535:
        print("Erro: porta inválida.", file=sys.stderr)
        return 2
    try:
        lock = acquire_app_lock(paths)
    except DesktopError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    sock = None
    try:
        if requested == 0:
            try:
                sock = bind_socket(host, 0)
            except OSError:
                print("Erro: não foi possível escolher uma porta livre.", file=sys.stderr)
                return 2
            port = int(sock.getsockname()[1])
        else:
            sock = try_bind(host, requested)
            if sock is None:
                suggestion = (
                    requested + 10 if requested + 10 <= 65535 else max(1, requested - 10)
                )
                print(
                    f"A porta {requested} já está em uso. Feche o outro programa "
                    f"ou use --port {suggestion}.",
                    file=sys.stderr,
                )
                return 2
            port = requested
        settings = replace(settings, server=replace(settings.server, port=port))
        app = create_app(paths, settings)
        url = f"http://127.0.0.1:{port}/"
        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        print(f"BuscaTeses em {url} — pressione Ctrl+C para encerrar.")
        server = uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="info")
        )
        server.run(sockets=[sock])
        return 0
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        lock.release()


def _setup(args: argparse.Namespace) -> int:
    paths = get_paths().ensure()
    settings = get_settings(paths)
    components = list(dict.fromkeys(args.only or ["model", "catalog"]))
    label = "o modelo de embedding" if components == ["model"] else "o catálogo"
    if len(components) > 1:
        label = "o modelo de embedding e o catálogo"
    if not args.yes:
        answer = input(f"Baixar {label} agora? [S/n] ").strip().casefold()
        if answer not in {"", "s", "sim", "y", "yes"}:
            print("Operação cancelada.")
            return 1

    installer = CatalogInstaller(paths, settings)
    jobs = JobManager()
    state: dict[str, Any] = {}

    def run(ctx: Any) -> None:
        if "model" in components:
            if settings.embedding.engine == "local":
                download_model(paths, settings.embedding.local_variant, ctx)
            elif settings.embedding.engine in {"openai", "openai_compatible"}:
                embedder = build_embedder(settings, paths)
                if embedder is None or not embedder.ready():
                    raise CatalogError(
                        "Configure o servidor de embeddings nas configurações antes de testar."
                    )
                embedder.embed_queries(["BuscaTeses"])
                ctx.update(stage="done", message="Servidor de embeddings OK.", progress=0.5)
            else:
                ctx.update(
                    stage="done",
                    message="Busca semântica desativada.",
                    progress=0.5,
                )
        if "catalog" in components:
            ctx.update(stage="download", message="Baixando o catálogo…", progress=0.0)
            state["result"] = installer.install(ctx=ctx)

    started = time.monotonic()
    job_id = jobs.submit("setup", run, exclusive=True)
    code = _wait_for_job(jobs, job_id)
    if code == 0:
        _print_summary(
            installer,
            state.get("result") if "catalog" in components else None,
            time.monotonic() - started,
            catalog_component="catalog" in components,
        )
    return code


def _update(args: argparse.Namespace) -> int:
    paths = get_paths().ensure()
    settings = get_settings(paths)
    installer = CatalogInstaller(paths, settings)
    print("Verificando atualizações do catálogo…")
    info = installer.check_update(force=True)
    if not info.available:
        print(f"O catálogo já está atualizado (versão {info.latest}).")
        return 0
    if not args.yes:
        answer = input(f"Baixar o catálogo {info.latest} agora? [S/n] ").strip().casefold()
        if answer not in {"", "s", "sim", "y", "yes"}:
            print("Operação cancelada.")
            return 1
    jobs = JobManager()
    state: dict[str, Any] = {}

    def run(ctx: Any) -> None:
        state["result"] = installer.install(info.latest, ctx=ctx)

    started = time.monotonic()
    job_id = jobs.submit("catalog_update", run, exclusive=True)
    code = _wait_for_job(jobs, job_id)
    if code == 0:
        _print_summary(
            installer,
            state.get("result"),
            time.monotonic() - started,
            catalog_component=True,
        )
    return code


def _status(args: argparse.Namespace) -> int:
    paths = get_paths().ensure()
    settings = get_settings(paths)
    installer = CatalogInstaller(paths, settings)
    catalog = installer.installed_status()
    library = LibraryStore(paths).counts()
    embedder = build_embedder(settings, paths)
    try:
        model_ready = bool(embedder and embedder.ready())
    except Exception:  # noqa: BLE001 - optional engine
        model_ready = False
    engine = settings.embedding.engine
    warnings: list[str] = []
    if not catalog["installed"]:
        warnings.append("catalog_missing")
    if engine == "local" and not model_ready:
        warnings.append("model_missing")
    if engine != "none" and not model_ready:
        warnings.append("semantic_unavailable")
    payload = {
        "app": {"version": __version__},
        "catalog": catalog,
        "model": {
            "engine": engine,
            "ready": model_ready,
            "label": _model_label(settings),
        },
        "library": library,
        "setup_required": (not catalog["installed"]) or (engine == "local" and not model_ready),
        "warnings": warnings,
        "data_dir": str(paths.home),
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"BuscaTeses {__version__}")
    print(f"Pasta de dados: {paths.home}")
    if catalog["installed"]:
        years = catalog.get("years") or []
        coverage = f"{years[0]}–{years[1]}" if len(years) == 2 else "desconhecido"
        print(
            f"Catálogo: instalado — versão {catalog['version']}, "
            f"{catalog['records']} registros ({coverage})"
        )
        print(f"Vetores ausentes: {catalog['missing_vectors']}")
        print(f"Construído em: {catalog.get('built_at') or 'desconhecido'}")
    else:
        print("Catálogo: não instalado (execute 'buscateses setup --only catalog').")
    print(f"Modelo: {_model_label(settings)} — {'pronto' if model_ready else 'indisponível'}")
    print(
        f"Biblioteca: {library['documents']} documento(s), "
        f"{library['chunks']} trecho(s), {library['pending']} pendente(s)"
    )
    if warnings:
        print("Avisos: " + ", ".join(warnings))
    return 0


def _search(args: argparse.Namespace) -> int:
    paths = get_paths().ensure()
    settings = get_settings(paths)
    installer = CatalogInstaller(paths, settings)
    release = installer.current_release_dir()
    if release is None:
        print(
            "Erro: o catálogo ainda não foi instalado. Execute 'buscateses setup'.",
            file=sys.stderr,
        )
        return 1
    if not 1 <= int(args.limit) <= 100:
        print("Erro: --limit deve estar entre 1 e 100.", file=sys.stderr)
        return 2
    embedder = build_embedder(settings, paths)
    catalog = CatalogSearch(release, embedder)
    result = catalog.search(
        SearchQuery(
            q=args.consulta,
            mode=args.mode,
            sort=args.sort,
            page_size=int(args.limit),
        )
    )
    payload = result.to_dict()
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    prefix = "mais de " if result.total_capped else ""
    print(
        f"{prefix}{result.total} resultado(s) em {result.took_ms} ms "
        f"(modo: {result.mode_used})"
    )
    if result.warnings:
        print("Avisos: " + ", ".join(result.warnings))
    for item in result.results:
        year = item.get("year") or "s.d."
        author = item.get("author") or "Autor não informado"
        institution = item.get("institution") or item.get("institution_acronym") or ""
        print(f"\n{item.get('title') or 'Sem título'} ({year})")
        print(f"  {author}" + (f" — {institution}" if institution else ""))
        print(f"  id: {item.get('id')}")
    return 0


def _library(args: argparse.Namespace) -> int:
    command = getattr(args, "library_command", None)
    if command is None:
        print(
            "Use: buscateses library add|list|remove|reindex",
            file=sys.stderr,
        )
        return 2
    paths = get_paths().ensure()
    settings = get_settings(paths)
    store = LibraryStore(paths)
    if command == "list":
        documents = store.list_documents()
        if args.as_json:
            print(json.dumps(documents, ensure_ascii=False, indent=2))
            return 0
        if not documents:
            print("A biblioteca está vazia.")
            return 0
        for document in documents:
            title = document.get("title") or document.get("filename") or "Sem título"
            year = document.get("year") or "s.d."
            print(f"{document['id']}  {title} ({year}) — {document['status']}")
            print(
                f"  {document.get('chunks') or 0} trecho(s), "
                f"{document.get('pages') or 0} página(s)"
            )
        return 0
    if command == "add":
        if not args.arquivos:
            print("Informe ao menos um arquivo.", file=sys.stderr)
            return 2
        embedder = build_embedder(settings, paths)
        ingestor = LibraryIngestor(paths, embedder, store=store)
        jobs = JobManager()
        exit_code = 0
        for source in args.arquivos:
            job_id = ingestor.submit(jobs, source)
            exit_code = max(exit_code, _wait_for_job(jobs, job_id))
        return exit_code
    if command == "reindex":
        if not args.yes:
            answer = input("Gerar os vetores que faltam agora? [S/n] ").strip().casefold()
            if answer not in {"", "s", "sim", "y", "yes"}:
                print("Operação cancelada.")
                return 1
        embedder = build_embedder(settings, paths)
        jobs = JobManager()
        job_id = jobs.submit(
            "library_reindex",
            lambda ctx: reindex_missing(store, embedder, ctx=ctx),
            exclusive=True,
        )
        return _wait_for_job(jobs, job_id)
    if command == "remove":
        if not args.yes:
            answer = input("Remover este documento da biblioteca? [s/N] ").strip().casefold()
            if answer not in {"s", "sim", "y", "yes"}:
                print("Operação cancelada.")
                return 1
        if store.delete_document(args.id):
            print("Documento removido da biblioteca.")
            return 0
        print("Erro: documento não encontrado.", file=sys.stderr)
        return 1
    print("Ação da biblioteca desconhecida.", file=sys.stderr)
    return 2


def _mcp(args: argparse.Namespace) -> int:
    from buscateses.mcp_server import run_http, run_stdio

    paths = get_paths().ensure()
    settings = get_settings(paths)
    if args.http:
        run_http(
            paths,
            settings,
            port=args.port,
            token=args.token,
            allow_no_token=bool(args.allow_no_token),
            allowed_hosts=args.allowed_host,
        )
    else:
        run_stdio(paths, settings)
    return 0


def _connect(args: argparse.Namespace) -> int:
    from buscateses.integrations import IntegrationError, connect, list_integrations

    data_home = get_paths().home
    if args.list_clients:
        rows = list_integrations(data_home=data_home)
        print(f"{'id':<16} {'cliente':<20} {'detectado':<10} conectado")
        for row in rows:
            print(
                f"{row['id']:<16} {row['name']:<20} "
                f"{'sim' if row['detected'] else 'não':<10} "
                f"{'sim' if row['connected'] else 'não'}"
            )
        return 0
    if not args.cliente:
        print("Informe o cliente ou use --list.", file=sys.stderr)
        return 2
    try:
        result = connect(args.cliente, data_home=data_home)
    except IntegrationError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _disconnect(args: argparse.Namespace) -> int:
    from buscateses.integrations import IntegrationError, disconnect

    try:
        result = disconnect(args.cliente)
    except IntegrationError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _remote(args: argparse.Namespace) -> int:
    """Create, stop or inspect the public MCP address."""
    from buscateses.remote import (
        STATE_ERROR,
        STATE_ONLINE,
        RemoteController,
        RemoteError,
        read_runtime_file,
        runtime_process_alive,
        save_remote_settings,
        write_runtime_file,
    )

    paths = get_paths().ensure()
    action = str(getattr(args, "acao", "") or "")

    if action == "status":
        record = read_runtime_file(paths)
        if record and runtime_process_alive(record):
            print(f"Endereço público ligado: {record.get('url')}")
            if record.get("include_library"):
                print("A biblioteca pessoal está incluída.")
            else:
                print("A biblioteca pessoal não está incluída.")
            return 0
        print("Endereço público desligado.")
        return 0

    if action == "stop":
        return _remote_stop(paths)

    if action != "start":
        print("Ação desconhecida. Use start, stop ou status.", file=sys.stderr)
        return 2

    record = read_runtime_file(paths)
    if record and runtime_process_alive(record):
        print(
            f"O endereço público já está ligado: {record.get('url')}",
            file=sys.stderr,
        )
        return 1

    settings = get_settings(paths)

    def publish(status: dict[str, Any] | None) -> None:
        if status is None:
            paths.remote_runtime_file.unlink(missing_ok=True)
        else:
            write_runtime_file(paths, status)

    controller = RemoteController(
        paths,
        settings,
        JobManager(),
        settings_saver=lambda new: save_remote_settings(paths, new),
        on_runtime=publish,
    )
    try:
        controller.start(bool(getattr(args, "include_library", False)))
    except RemoteError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    status = _wait_remote(controller, timeout=180.0)
    if status["state"] == STATE_ERROR:
        print(f"Erro: {status['message']}", file=sys.stderr)
        controller.stop()
        return 1
    if status["state"] != STATE_ONLINE:
        print("Erro: o endereço público demorou para ficar pronto.", file=sys.stderr)
        controller.stop()
        return 1

    print(f"Endereço público: {status['url']}")
    print("Use este endereço no ChatGPT ou no Claude.ai. Pressione Ctrl+C para desligar.")
    try:
        while True:
            time.sleep(0.5)
            current = controller.status()
            if current["state"] == STATE_ERROR:
                print(f"Erro: {current['message']}", file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        print("\nDesligando o endereço público…")
    finally:
        controller.stop()
    return 0


def _wait_remote(controller: Any, *, timeout: float) -> dict[str, Any]:
    """Poll a controller until it is online, failed or the timeout expires."""
    from buscateses.remote import STATE_ERROR, STATE_ONLINE

    deadline = time.monotonic() + timeout
    status = controller.status()
    while time.monotonic() < deadline:
        status = controller.status()
        if status["state"] in {STATE_ONLINE, STATE_ERROR}:
            return status
        time.sleep(0.25)
    return status


def _remote_stop(paths: Paths) -> int:
    """Terminate a tunnel started by ``buscateses remote start``."""
    import signal

    from buscateses.remote import read_runtime_file, runtime_process_alive

    record = read_runtime_file(paths)
    if not record or not runtime_process_alive(record):
        paths.remote_runtime_file.unlink(missing_ok=True)
        print("O endereço público já está desligado.")
        return 0
    try:
        pid = int(record.get("pid") or 0)
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as exc:
        print(f"Erro: não foi possível desligar o endereço público ({exc}).", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and runtime_process_alive(record):
        time.sleep(0.1)
    paths.remote_runtime_file.unlink(missing_ok=True)
    print("Endereço público desligado.")
    return 0


def _version(args: argparse.Namespace) -> int:
    del args
    print(f"BuscaTeses {__version__}")
    return 0


_PROGRESS_INTERVAL = 2.0


class _ProgressPrinter:
    """Render job progress as a TTY status line or periodic log lines."""

    def __init__(self) -> None:
        self._tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._header: tuple[str, str] | None = None
        self._detail = ""
        self._last_render = 0.0
        self._line_open = False

    def update(self, snapshot: dict[str, Any], *, force: bool = False) -> None:
        """Print a stage header when it changes and throttled progress lines."""
        stage = str(snapshot.get("stage") or "")
        message = str(snapshot.get("message") or "")
        header = (stage, message)
        now = time.monotonic()
        if header != self._header:
            self._close_line()
            if message:
                print(message, flush=True)
            self._header = header
            force = True
        detail = _progress_detail(snapshot)
        if not detail:
            return
        if not force:
            if detail == self._detail or now - self._last_render < _PROGRESS_INTERVAL:
                return
        self._render(detail)
        self._detail = detail
        self._last_render = now

    def finish(self) -> None:
        """Terminate an in-place TTY line."""
        self._close_line()

    def _render(self, detail: str) -> None:
        if self._tty:
            sys.stdout.write(f"\r  {detail}\x1b[K")
            sys.stdout.flush()
            self._line_open = True
        else:
            print(f"  {detail}", flush=True)

    def _close_line(self) -> None:
        if self._line_open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._line_open = False


def _progress_detail(snapshot: dict[str, Any]) -> str:
    """Format percentage and either bytes (transfer) or rows (build)."""
    stage = str(snapshot.get("stage") or "")
    progress = snapshot.get("progress")
    done = int(snapshot.get("bytes_done") or 0)
    total = snapshot.get("bytes_total")
    parts: list[str] = []
    if progress is not None:
        parts.append(f"{int(float(progress) * 100)}%")
    if total:
        total = int(total)
        if stage in {"download", "verify"}:
            parts.append(f"{_human_bytes(done)}/{_human_bytes(total)}")
        else:
            parts.append(f"{_format_count(done)}/{_format_count(total)} registros")
    elif done and stage in {"download", "verify"}:
        parts.append(_human_bytes(done))
    return " · ".join(parts)


def _wait_for_job(jobs: JobManager, job_id: str) -> int:
    printer = _ProgressPrinter()
    while True:
        snapshot = jobs.get(job_id)
        if snapshot["status"] in {"done", "error", "cancelled"}:
            printer.update(snapshot, force=True)
            break
        printer.update(snapshot)
        time.sleep(0.25)
    printer.finish()
    if snapshot["status"] == "done":
        print("Concluído.")
        return 0
    if snapshot["status"] == "cancelled":
        print("Tarefa cancelada.", file=sys.stderr)
        return 130
    print(f"Erro: {snapshot.get('error') or 'a tarefa falhou.'}", file=sys.stderr)
    return 1


def _print_summary(
    installer: CatalogInstaller,
    result: dict[str, Any] | None,
    elapsed: float,
    *,
    catalog_component: bool,
) -> None:
    """Print records, on-disk size and elapsed time after setup/update."""
    print()
    print("Resumo:")
    if catalog_component:
        release = installer.current_release_dir()
        records = result.get("records") if result else None
        if records is None:
            records = installer.installed_status().get("records")
        if records is not None:
            print(f"  Registros: {_format_count(int(records))}")
        if release is not None:
            print(
                f"  Espaço em disco: {_human_bytes(_directory_size(release))} "
                f"(releases/{release.name})"
            )
    print(f"  Tempo total: {_format_duration(elapsed)}")


def _directory_size(path: Any) -> int:
    """Return the total size in bytes of a directory tree."""
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except OSError:
        return total
    return total


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _human_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} TB"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}min {secs}s"
    if minutes:
        return f"{minutes}min {secs}s"
    return f"{secs}s"


def _model_label(settings: Settings) -> str:
    engine = settings.embedding.engine
    if engine == "local":
        return f"EmbeddingGemma 300M local ({settings.embedding.local_variant})"
    if engine in {"openai", "openai_compatible"}:
        return settings.embedding.model or "Servidor de embeddings"
    return "Busca semântica desativada"


def _ensure_streams() -> None:
    """Avoid crashes in windowed builds and flush redirected output line by line.

    When stdout/stderr are redirected to a file they are block-buffered by
    default; long-running commands (``setup``, ``update``, library ingestion)
    would then show nothing until the process exits.  Reconfiguring both
    streams with ``line_buffering=True`` makes every progress line visible
    immediately.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(line_buffering=True)
        except (OSError, ValueError):
            # Some wrappers (for example pytest's capture) cannot reconfigure.
            continue


_BUILTIN_BUILDERS: dict[str, CommandBuilder] = {
    "app": _build_app,
    "serve": _build_serve,
    "setup": _build_setup,
    "update": _build_update,
    "status": _build_status,
    "doctor": _build_status,
    "search": _build_search,
    "library": _build_library,
    "mcp": _build_mcp,
    "remote": _build_remote,
    "connect": _build_connect,
    "disconnect": _build_disconnect,
    "version": _build_version,
}
_BUILTIN_HELP = {
    "app": "Abre a janela do BuscaTeses",
    "serve": "Inicia o servidor web local",
    "setup": "Baixa o modelo de embedding e/ou o catálogo",
    "update": "Atualiza o catálogo local",
    "status": "Mostra o diagnóstico da instalação",
    "doctor": "Alias de status",
    "search": "Busca no catálogo pelo terminal",
    "library": "Gerencia a biblioteca pessoal",
    "mcp": "Inicia o servidor MCP somente leitura",
    "remote": "Cria um endereço público para ChatGPT e Claude.ai",
    "connect": "Conecta o BuscaTeses a um assistente de IA",
    "disconnect": "Desconecta o BuscaTeses de um assistente de IA",
    "version": "Mostra a versão",
}
_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "app": _app,
    "serve": _serve,
    "setup": _setup,
    "update": _update,
    "status": _status,
    "doctor": _status,
    "search": _search,
    "library": _library,
    "mcp": _mcp,
    "remote": _remote,
    "connect": _connect,
    "disconnect": _disconnect,
    "version": _version,
}
COMMAND_HANDLERS.update(_COMMAND_HANDLERS)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
