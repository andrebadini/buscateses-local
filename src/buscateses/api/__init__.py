"""FastAPI application factory for the local BuscaTeses server."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from buscateses import __version__
from buscateses.api import (
    routes_app,
    routes_export,
    routes_favorites,
    routes_integrations,
    routes_library,
    routes_remote,
    routes_search,
    routes_settings,
    routes_setup,
    routes_status,
)
from buscateses.api.security import LocalSecurityMiddleware, UploadSizeLimitMiddleware
from buscateses.api.state import AppState
from buscateses.config import Paths, Settings, get_paths, get_settings
from buscateses.jobs import JobConflict, JobManager
from buscateses.warmup import start_warmup

_BUILTIN_ROUTERS = (
    routes_status.router,
    routes_setup.router,
    routes_search.router,
    routes_export.router,
    routes_favorites.router,
    routes_settings.router,
    routes_library.router,
    routes_integrations.router,
    routes_app.router,
    routes_remote.router,
)
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# The optional VLibras widget is the only third-party feature the UI may load;
# vlibras.gov.br serves its plugin and assets through cdn.jsdelivr.net.
INDEX_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://vlibras.gov.br https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://vlibras.gov.br; "
    "img-src 'self' data: https://vlibras.gov.br https://cdn.jsdelivr.net; "
    "connect-src 'self' https://vlibras.gov.br; "
    "frame-src https://vlibras.gov.br; "
    "font-src 'self' https://vlibras.gov.br https://cdn.jsdelivr.net; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


def create_app(
    paths: Paths | None = None,
    settings: Settings | None = None,
    *,
    embedder: Any | None = None,
    catalog: Any | None = None,
    jobs: JobManager | None = None,
    quit_callback: Callable[[], Any] | None = None,
    extra_routers: Iterable[APIRouter] | None = None,
) -> FastAPI:
    """Create the FastAPI app with explicit dependencies for testing.

    Additional routers can be appended through ``extra_routers``; this is the
    extension point for the future library, MCP and integration modules.
    """
    resolved_paths = paths or get_paths()
    resolved_settings = settings or get_settings(resolved_paths)
    resolved_paths.ensure()
    state = AppState(
        resolved_paths,
        resolved_settings,
        embedder=embedder,
        catalog=catalog,
        jobs=jobs,
        quit_callback=quit_callback,
    )
    app = FastAPI(
        title="BuscaTeses",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = state
    if not state.catalog_injected:
        # Warm the model and the catalog off the UI thread; create_app returns
        # immediately even when the release is still being resolved.
        start_warmup(embedder=state.embedder, catalog_provider=lambda: state.catalog)
    app.add_middleware(LocalSecurityMiddleware, port=resolved_settings.server.port)
    app.add_middleware(UploadSizeLimitMiddleware)

    for router in _BUILTIN_ROUTERS:
        app.include_router(router)
    if extra_routers:
        for router in extra_routers:
            app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return _index_response(_WEB_DIR)

    @app.get("/{path:path}", include_in_schema=False)
    def static_files(path: str) -> FileResponse:
        candidate = _safe_web_path(_WEB_DIR, path)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Página não encontrada.")
        headers = (
            {"Cache-Control": "no-cache", "Content-Security-Policy": INDEX_CSP}
            if candidate.suffix == ".html"
            else {}
        )
        return FileResponse(candidate, headers=headers)

    _install_error_handlers(app)
    return app


def _safe_web_path(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    root = root.resolve()
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate.is_dir():
        index = candidate / "index.html"
        return index if index.is_file() else None
    return candidate if candidate.is_file() else None


def _index_response(web_dir: Path) -> FileResponse:
    index = web_dir / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="Interface web não encontrada.")
    return FileResponse(
        index,
        media_type="text/html",
        headers={"Cache-Control": "no-cache", "Content-Security-Policy": INDEX_CSP},
    )


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if not isinstance(detail, str) or not detail.strip():
            detail = "Não foi possível concluir a operação."
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": "Parâmetros inválidos na requisição."},
            status_code=422,
        )

    @app.exception_handler(JobConflict)
    async def job_conflict(_request: Request, exc: JobConflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            {"detail": "Ocorreu um erro inesperado. Tente novamente."},
            status_code=500,
        )


__all__ = ["INDEX_CSP", "create_app"]
