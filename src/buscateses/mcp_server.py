"""Read-only MCP server exposing the catalog and personal library (SPEC §9)."""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import sys
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from typing import Any, NotRequired

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

# pydantic requires typing_extensions.TypedDict on Python < 3.12.
from typing_extensions import TypedDict  # noqa: UP035

from buscateses.catalog.installer import CatalogInstaller
from buscateses.catalog.search import (
    CatalogSearch,
    SearchError,
    SearchNotFoundError,
    SearchQuery,
)
from buscateses.config import (
    Paths,
    Settings,
    get_paths,
    get_settings,
    normalize_allowed_hosts,
    read_config_file,
)
from buscateses.embeddings import Embedder, build_embedder
from buscateses.library.store import LibraryError, LibraryStore
from buscateses.warmup import start_warmup

_CATALOG_INSTRUCTIONS = (
    "BuscaTeses: busca no Catálogo de Teses e Dissertações da CAPES instalado "
    "neste computador. Use search_theses para buscar trabalhos, similar_theses "
    "para relacionados e get_thesis para detalhes."
)
_LIBRARY_INSTRUCTIONS = (
    " Use as ferramentas da biblioteca para documentos próprios do usuário."
)
_LOCAL_PRIVACY = " Nada sai do computador do usuário."
_REMOTE_PRIVACY = " As respostas vêm do catálogo neste computador."
_CITATION_RULE = " Cite sempre título, autor, ano e URL quando houver."


def _instructions(include_library: bool, *, remote: bool = False) -> str:
    """Build the MCP instructions for a local or a public endpoint."""
    text = _CATALOG_INSTRUCTIONS
    if include_library:
        text += _LIBRARY_INSTRUCTIONS
    text += _REMOTE_PRIVACY if remote else _LOCAL_PRIVACY
    return text + _CITATION_RULE


INSTRUCTIONS = _instructions(True)
_CATALOG_MISSING = (
    "O catálogo do BuscaTeses ainda não foi instalado. Abra o app BuscaTeses e "
    "conclua a instalação para usar esta ferramenta."
)
_LIBRARY_MISSING = (
    "A biblioteca ainda está vazia. Abra o app BuscaTeses e adicione documentos."
)
_MODES = {"hybrid", "text", "semantic"}
_LIBRARY_TOOLS = ("search_library", "list_library_documents", "get_library_passage")
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class ThesisHit(TypedDict):
    """Compact thesis record returned by search tools."""

    id: str
    title: str | None
    author: str | None
    year: int | None
    degree: str | None
    institution: str | None
    uf: str | None
    snippet: str
    url: str | None
    score: float
    matched_by: list[str]


class SearchThesesOutput(TypedDict):
    """Result of ``search_theses``."""

    query: str
    mode_used: str
    total: int
    total_is_estimate: bool
    relaxed: bool
    warnings: list[str]
    results: list[ThesisHit]


class ThesisListOutput(TypedDict):
    """List of related theses."""

    results: list[ThesisHit]


class ThesisRecord(TypedDict):
    """Full thesis metadata returned by ``get_thesis``."""

    id: str
    title: str | None
    author: str | None
    advisors: NotRequired[str | None]
    year: int | None
    defense_date: NotRequired[str | None]
    degree: str | None
    institution: str | None
    institution_acronym: NotRequired[str | None]
    uf: str | None
    region: NotRequired[str | None]
    program: NotRequired[str | None]
    great_area: NotRequired[str | None]
    knowledge_area: NotRequired[str | None]
    keywords: NotRequired[str | None]
    abstract: NotRequired[str | None]
    language: NotRequired[str | None]
    pages: NotRequired[int | None]
    url: str | None


class LibraryHit(TypedDict):
    """One matching excerpt from the personal library."""

    chunk_id: int
    doc_id: str
    title: str | None
    filename: str | None
    page_start: int | None
    page_end: int | None
    snippet: str
    score: float
    matched_by: list[str]


class LibrarySearchOutput(TypedDict):
    """Result of ``search_library``."""

    query: str
    mode_used: str
    total: int
    page: int
    page_size: int
    warnings: list[str]
    results: list[LibraryHit]


class LibraryDocument(TypedDict):
    """A document stored in the personal library."""

    id: str
    filename: str
    title: str | None
    author: str | None
    year: int | None
    kind: str
    pages: int
    chunks: int
    status: str
    error: str | None
    embedded: bool
    added_at: str
    file_url: str


class LibraryDocumentsOutput(TypedDict):
    """Result of ``list_library_documents``."""

    documents: list[LibraryDocument]


class PassageChunk(TypedDict):
    """A chunk that belongs to a passage context."""

    chunk_id: int
    ord: int
    page_start: int | None
    page_end: int | None
    text: str
    is_target: bool


class LibraryPassageOutput(TypedDict):
    """Result of ``get_library_passage``."""

    chunk_id: int
    doc_id: str
    title: str | None
    filename: str | None
    page_start: int | None
    page_end: int | None
    text: str
    chunks: list[PassageChunk]


class CatalogStatusOutput(TypedDict):
    """Result of ``catalog_status``."""

    installed: bool
    version: str | None
    records: int
    years: list[int]
    library_documents: int
    library_chunks: int
    message: str


_UNSET = object()


class _Services:
    """Lazily resolve catalog, library and embedder for the tools."""

    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        *,
        embedder: Any = _UNSET,
        catalog: Any | None = None,
        store: LibraryStore | None = None,
    ) -> None:
        self.paths = paths
        self.settings = settings
        self.installer = CatalogInstaller(paths, settings)
        self._embedder = None if embedder is _UNSET else embedder
        self._embedder_resolved = embedder is not _UNSET
        self._catalog = catalog
        self._catalog_injected = catalog is not None
        self._catalog_key: tuple[str, int] | None = None
        self._store = store
        self._warmup_started = False

    @property
    def embedder(self) -> Embedder | None:
        """Build the embedder on first use (the model itself loads lazily)."""
        if not self._embedder_resolved:
            self._embedder = build_embedder(self.settings, self.paths)
            self._embedder_resolved = True
        return self._embedder

    def catalog_search(self) -> Any:
        """Return the catalog, reopening it when ``current.json`` changes."""
        if self._catalog_injected:
            return self._catalog
        release = self.installer.current_release_dir()
        if release is None:
            self._close_catalog()
            raise ToolError(_CATALOG_MISSING)
        key = (str(release), _mtime_ns(self.paths.current_file))
        if self._catalog is not None and self._catalog_key == key:
            self._start_warmup(self._catalog)
            return self._catalog
        self._close_catalog()
        self._catalog = CatalogSearch(release, self.embedder)
        self._catalog_key = key
        self._start_warmup(self._catalog)
        return self._catalog

    def _start_warmup(self, catalog: Any) -> None:
        """Warm caches once, on the first MCP tool call, without blocking it."""
        if self._warmup_started:
            return
        self._warmup_started = True
        start_warmup(embedder=self.embedder, catalog=catalog)

    def _close_catalog(self) -> None:
        catalog = self._catalog
        self._catalog = None
        self._catalog_key = None
        closer = getattr(catalog, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001 - an update must not be blocked
                logging.getLogger(__name__).warning(
                    "Falha ao fechar o catálogo anterior.", exc_info=True
                )

    def library(self) -> LibraryStore:
        """Open the library in read-only mode."""
        if self._store is not None:
            return self._store
        if not self.paths.library_db.is_file():
            raise ToolError(_LIBRARY_MISSING)
        self._store = LibraryStore(self.paths, read_only=True)
        return self._store


def create_server(
    paths: Paths | None = None,
    settings: Settings | None = None,
    *,
    embedder: Any = _UNSET,
    catalog: Any | None = None,
    store: LibraryStore | None = None,
    include_library: bool = True,
    streamable_http_path: str = "/mcp",
    stateless_http: bool = False,
    json_response: bool = False,
    enable_dns_rebinding_protection: bool = True,
    remote: bool = False,
) -> FastMCP:
    """Build the FastMCP server with all read-only tools registered.

    ``include_library=False`` hides the personal-library tools. The public
    Quick Tunnel endpoint uses that mode unless the user explicitly opts in,
    so a shared address never exposes the library by default.

    When ``streamable_http_path`` carries a secret (for example
    ``/mcp/<segredo>``), DNS-rebinding protection may be disabled: Cloudflare
    forwards its own public ``Host`` header, and the unguessable path already
    prevents other web pages from reaching the endpoint.
    """
    resolved_paths = paths or get_paths()
    resolved_settings = settings or get_settings(resolved_paths)
    services = _Services(
        resolved_paths,
        resolved_settings,
        embedder=embedder,
        catalog=catalog,
        store=store,
    )
    port = int(resolved_settings.mcp_http.port)
    server = FastMCP(
        name="BuscaTeses",
        instructions=_instructions(include_library, remote=remote),
        host="127.0.0.1",
        port=port,
        log_level="WARNING",
        streamable_http_path=streamable_http_path,
        stateless_http=stateless_http,
        json_response=json_response,
        transport_security=_transport_security(
            port,
            resolved_settings.mcp_http.allowed_hosts,
            enabled=enable_dns_rebinding_protection,
        ),
    )

    @server.tool(
        name="search_theses",
        description=(
            "Busca teses e dissertações no catálogo local da CAPES. Aceita filtros "
            "de ano, grau, UF, instituição e grande área."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def search_theses(
        query: str,
        year_min: int | None = None,
        year_max: int | None = None,
        degree: str | None = None,
        uf: str | None = None,
        institution: str | None = None,
        area: str | None = None,
        mode: str = "hybrid",
        limit: int = 20,
    ) -> SearchThesesOutput:
        """Busca trabalhos no catálogo da CAPES e devolve os metadados principais."""
        query_text = _require_text(query)
        mode_value = _require_mode(mode)
        size = _bounded(limit, 1, 50, "O limite deve estar entre 1 e 50.")
        search = SearchQuery(
            q=query_text,
            mode=mode_value,
            year_min=year_min,
            year_max=year_max,
            degree=_multi(degree),
            uf=_multi(uf),
            institution=_multi(institution),
            great_area=_multi(area),
            page_size=size,
        )
        try:
            result = services.catalog_search().search(search)
        except SearchError as exc:
            raise ToolError(str(exc)) from exc
        payload = result.to_dict()
        return {
            "query": payload["query"],
            "mode_used": payload["mode_used"],
            "total": int(payload["total"]),
            "total_is_estimate": bool(payload["total_is_estimate"]),
            "relaxed": bool(payload["relaxed"]),
            "warnings": [str(item) for item in payload["warnings"]],
            "results": [_hit(item) for item in payload["results"]],
        }

    @server.tool(
        name="get_thesis",
        description="Devolve os metadados completos de uma tese pelo identificador.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_thesis(id: str) -> ThesisRecord:
        """Retorna título, autores, instituição, resumo e URL de um trabalho."""
        identifier = _require_text(id, label="o identificador")
        try:
            data = services.catalog_search().get(identifier)
        except SearchNotFoundError as exc:
            raise ToolError("Tese não encontrada.") from exc
        except SearchError as exc:
            raise ToolError(str(exc)) from exc
        record: ThesisRecord = {
            "id": str(data.get("id") or identifier),
            "title": data.get("title"),
            "author": data.get("author"),
            "advisors": data.get("advisors"),
            "year": _int_or_none(data.get("year")),
            "defense_date": data.get("defense_date"),
            "degree": data.get("degree"),
            "institution": data.get("institution"),
            "institution_acronym": data.get("institution_acronym"),
            "uf": data.get("uf"),
            "region": data.get("region"),
            "program": data.get("program"),
            "great_area": data.get("great_area"),
            "knowledge_area": data.get("knowledge_area"),
            "keywords": data.get("keywords"),
            "abstract": data.get("abstract"),
            "language": data.get("language"),
            "pages": _int_or_none(data.get("pages")),
            "url": data.get("url"),
        }
        return record

    @server.tool(
        name="similar_theses",
        description="Lista trabalhos semanticamente semelhantes a um identificador.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def similar_theses(id: str, limit: int = 10) -> ThesisListOutput:
        """Retorna até 20 trabalhos relacionados ao informado."""
        identifier = _require_text(id, label="o identificador")
        size = _bounded(limit, 1, 20, "O limite deve estar entre 1 e 20.")
        try:
            rows = services.catalog_search().similar(identifier, size)
        except SearchError as exc:
            raise ToolError(str(exc)) from exc
        return {"results": [_hit(item) for item in rows]}

    @server.tool(
        name="search_library",
        description=(
            "Busca trechos nos documentos da biblioteca pessoal do usuário, "
            "opcionalmente limitada a um documento."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def search_library(
        query: str,
        document_id: str | None = None,
        limit: int = 20,
    ) -> LibrarySearchOutput:
        """Busca por trechos nos documentos adicionados pelo usuário."""
        query_text = _require_text(query)
        size = _bounded(limit, 1, 30, "O limite deve estar entre 1 e 30.")
        try:
            result = services.library().search(
                query_text,
                embedder=services.embedder,
                doc_id=document_id,
                page_size=size,
            )
        except LibraryError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "query": str(result["query"]),
            "mode_used": str(result["mode_used"]),
            "total": int(result["total"]),
            "page": int(result["page"]),
            "page_size": int(result["page_size"]),
            "warnings": [str(item) for item in result["warnings"]],
            "results": [_library_hit(item) for item in result["results"]],
        }

    @server.tool(
        name="list_library_documents",
        description="Lista os documentos da biblioteca pessoal com metadados básicos.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def list_library_documents() -> LibraryDocumentsOutput:
        """Retorna os documentos da biblioteca do usuário."""
        documents = services.library().list_documents()
        return {"documents": [_library_document(item) for item in documents]}

    @server.tool(
        name="get_library_passage",
        description=(
            "Devolve um trecho da biblioteca com contexto opcional de até 3 trechos "
            "vizinhos."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_library_passage(
        chunk_id: int,
        context_chunks: int = 1,
    ) -> LibraryPassageOutput:
        """Retorna o trecho indicado e seus vizinhos para leitura."""
        context = _bounded(
            context_chunks, 0, 3, "O contexto deve estar entre 0 e 3 trechos."
        )
        try:
            passage = services.library().get_passage(int(chunk_id), context)
        except LibraryError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "chunk_id": int(passage["chunk_id"]),
            "doc_id": str(passage["doc_id"]),
            "title": passage.get("title"),
            "filename": passage.get("filename"),
            "page_start": _int_or_none(passage.get("page_start")),
            "page_end": _int_or_none(passage.get("page_end")),
            "text": str(passage["text"]),
            "chunks": [
                {
                    "chunk_id": int(item["chunk_id"]),
                    "ord": int(item["ord"]),
                    "page_start": _int_or_none(item.get("page_start")),
                    "page_end": _int_or_none(item.get("page_end")),
                    "text": str(item["text"]),
                    "is_target": bool(item["is_target"]),
                }
                for item in passage["chunks"]
            ],
        }

    @server.tool(
        name="catalog_status",
        description=(
            "Informa se o catálogo está instalado e quantos documentos há na biblioteca."
            if include_library
            else "Informa se o catálogo está instalado."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def catalog_status() -> CatalogStatusOutput:
        """Retorna a versão do catálogo e os contadores da biblioteca."""
        status = services.installer.installed_status()
        counts = {"documents": 0, "chunks": 0}
        if include_library:
            try:
                counts = services.library().counts()
            except ToolError:
                pass
        installed = bool(status.get("installed"))
        return {
            "installed": installed,
            "version": status.get("version"),
            "records": int(status.get("records") or 0),
            "years": [int(year) for year in status.get("years") or []],
            "library_documents": int(counts.get("documents") or 0),
            "library_chunks": int(counts.get("chunks") or 0),
            "message": (
                "Catálogo instalado."
                if installed
                else "Abra o app BuscaTeses para concluir a instalação do catálogo."
            ),
        }

    if not include_library:
        # Removing after registration keeps every tool definition in one place
        # while guaranteeing the public endpoint never lists library tools.
        for name in _LIBRARY_TOOLS:
            server.remove_tool(name)

    return server


def build_http_app(server: FastMCP, token: str | None) -> Any:
    """Return the Streamable HTTP ASGI app, optionally behind Bearer auth."""
    app = server.streamable_http_app()
    if token:
        return _BearerAuthMiddleware(app, token)
    return app


class _BearerAuthMiddleware:
    """Minimal ASGI middleware enforcing a Bearer token with constant-time compare."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = str(token)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        authorization = b""
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                authorization = value.strip()
                break
        expected = f"Bearer {self.token}".encode()
        if not hmac.compare_digest(authorization, expected):
            body = json.dumps(
                {"detail": "Token de acesso ausente ou inválido."},
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"www-authenticate", b'Bearer realm="BuscaTeses"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def ensure_http_token(
    paths: Paths,
    settings: Settings,
    *,
    token: str | None = None,
    allow_no_token: bool = False,
) -> tuple[Settings, str | None]:
    """Resolve the HTTP token, generating and persisting one when needed."""
    if allow_no_token:
        return settings, None
    explicit = (token or "").strip()
    if explicit:
        if settings.mcp_http.token != explicit:
            _persist_token(paths, explicit)
            settings = replace(
                settings, mcp_http=replace(settings.mcp_http, token=explicit)
            )
        return settings, explicit
    current = settings.mcp_http.token.strip()
    if current:
        return settings, current
    generated = secrets.token_urlsafe(32)
    _persist_token(paths, generated)
    settings = replace(settings, mcp_http=replace(settings.mcp_http, token=generated))
    return settings, generated


def _persist_token(paths: Paths, token: str) -> None:
    """Write only the token over the on-disk config.

    Runtime settings may contain fallback ports or ``BUSCATESES_*`` overrides
    that must never be frozen into ``config.json``.
    """
    base = read_config_file(paths)
    mcp_http = base.get("mcp_http")
    if not isinstance(mcp_http, dict):
        mcp_http = {}
    mcp_http["token"] = token
    base["mcp_http"] = mcp_http
    Settings.from_dict(base, apply_env=False).save(paths)


def run_stdio(paths: Paths | None = None, settings: Settings | None = None) -> None:
    """Run the MCP server over stdio; logs never go to stdout."""
    resolved_paths = (paths or get_paths()).ensure()
    resolved_settings = settings or get_settings(resolved_paths)
    _configure_logging(resolved_paths)
    create_server(resolved_paths, resolved_settings).run("stdio")


def run_http(
    paths: Paths | None = None,
    settings: Settings | None = None,
    *,
    port: int | None = None,
    token: str | None = None,
    allow_no_token: bool = False,
    allowed_hosts: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Run the MCP server over Streamable HTTP on 127.0.0.1."""
    import uvicorn

    resolved_paths = (paths or get_paths()).ensure()
    resolved_settings = settings or get_settings(resolved_paths)
    if port is not None:
        if not 1 <= int(port) <= 65535:
            raise ValueError("A porta do MCP deve estar entre 1 e 65535.")
        resolved_settings = replace(
            resolved_settings, mcp_http=replace(resolved_settings.mcp_http, port=int(port))
        )
    if allowed_hosts:
        merged_hosts = normalize_allowed_hosts(
            [*resolved_settings.mcp_http.allowed_hosts, *allowed_hosts]
        )
        resolved_settings = replace(
            resolved_settings,
            mcp_http=replace(resolved_settings.mcp_http, allowed_hosts=merged_hosts),
        )
    resolved_settings, active_token = ensure_http_token(
        resolved_paths,
        resolved_settings,
        token=token,
        allow_no_token=allow_no_token,
    )
    _configure_logging(resolved_paths)
    server = create_server(resolved_paths, resolved_settings)
    app = build_http_app(server, active_token)
    address = f"http://127.0.0.1:{resolved_settings.mcp_http.port}/mcp"
    print(f"Servidor MCP do BuscaTeses em {address}", file=sys.stderr)
    if active_token:
        print(f"Token de acesso: {active_token}", file=sys.stderr)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=resolved_settings.mcp_http.port,
        log_level="warning",
        log_config=None,
    )


def _configure_logging(paths: Paths) -> None:
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
    if not any(type(item) is logging.StreamHandler for item in root.handlers):
        root.addHandler(logging.StreamHandler(sys.stderr))


def _transport_security(
    port: int,
    allowed_hosts: tuple[str, ...] | list[str] = (),
    *,
    enabled: bool = True,
) -> TransportSecuritySettings:
    """Allow loopback plus explicitly configured HTTPS tunnel host names.

    Tunnels (Tailscale Funnel, Cloudflare Tunnel) forward requests with their
    public ``Host`` header, which the default loopback-only allowlist would
    reject. Users must opt in to each public host.

    ``enabled=False`` is used only by the Quick Tunnel endpoint: Cloudflare
    rewrites the ``Host`` header, and that endpoint is already protected by an
    unguessable secret in the URL path.
    """
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    origins = [f"http://{host}" for host in hosts]
    for raw in allowed_hosts:
        host = str(raw).strip().lower()
        if not host:
            continue
        # Public HTTPS endpoints normally use the default port, so the Host
        # header has no port; also accept the explicit :443 spelling.
        variants = [host] if ":" in host else [host, f"{host}:443"]
        for variant in variants:
            if variant not in hosts:
                hosts.append(variant)
            for scheme in ("https", "http"):
                origin = f"{scheme}://{variant}"
                if origin not in origins:
                    origins.append(origin)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enabled,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _mtime_ns(path: Any) -> int:
    """Return a file's modification time, or 0 when it does not exist."""
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def _require_text(value: Any, *, label: str = "uma consulta") -> str:
    text = str(value or "").strip()
    if not text:
        raise ToolError(f"Digite {label} com pelo menos uma palavra.")
    return text[:2000]


def _require_mode(value: str) -> str:
    mode = str(value or "hybrid").strip().lower()
    if mode not in _MODES:
        raise ToolError("Modo de busca inválido. Use hybrid, text ou semantic.")
    return mode


def _bounded(value: Any, minimum: int, maximum: int, message: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(message) from exc
    if not minimum <= number <= maximum:
        raise ToolError(message)
    return number


def _multi(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = [
        part.strip()
        for part in str(value).replace(";", ",").split(",")
        if part.strip()
    ]
    return tuple(parts)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hit(item: dict[str, Any]) -> ThesisHit:
    return {
        "id": str(item.get("id") or ""),
        "title": item.get("title"),
        "author": item.get("author"),
        "year": _int_or_none(item.get("year")),
        "degree": item.get("degree"),
        "institution": item.get("institution"),
        "uf": item.get("uf"),
        "snippet": str(item.get("snippet") or ""),
        "url": item.get("url"),
        "score": float(item.get("score") or 0.0),
        "matched_by": [str(value) for value in item.get("matched_by") or []],
    }


def _library_hit(item: dict[str, Any]) -> LibraryHit:
    return {
        "chunk_id": int(item["chunk_id"]),
        "doc_id": str(item["doc_id"]),
        "title": item.get("title"),
        "filename": item.get("filename"),
        "page_start": _int_or_none(item.get("page_start")),
        "page_end": _int_or_none(item.get("page_end")),
        "snippet": str(item.get("snippet") or ""),
        "score": float(item.get("score") or 0.0),
        "matched_by": [str(value) for value in item.get("matched_by") or []],
    }


def _library_document(item: dict[str, Any]) -> LibraryDocument:
    return {
        "id": str(item["id"]),
        "filename": str(item.get("filename") or ""),
        "title": item.get("title"),
        "author": item.get("author"),
        "year": _int_or_none(item.get("year")),
        "kind": str(item.get("kind") or ""),
        "pages": int(item.get("pages") or 0),
        "chunks": int(item.get("chunks") or 0),
        "status": str(item.get("status") or ""),
        "error": item.get("error"),
        "embedded": bool(item.get("embedded")),
        "added_at": str(item.get("added_at") or ""),
        "file_url": str(item.get("file_url") or ""),
    }


__all__ = [
    "INSTRUCTIONS",
    "build_http_app",
    "create_server",
    "ensure_http_token",
    "run_http",
    "run_stdio",
]
