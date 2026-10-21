"""Host, Origin and CSRF-header protection for the local web app."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-buscateses"
CSRF_VALUE = "1"
UPLOAD_PATH = "/api/library/upload"
_DENIED_HOST = "Acesso restrito ao computador local."
_DENIED_ORIGIN = "Origem da requisição não permitida."
_MISSING_HEADER = "Requisição bloqueada por segurança. Atualize a página e tente novamente."


def allowed_hosts(port: int) -> set[str]:
    """Return the exact Host header values accepted for ``port``."""
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def allowed_origins(port: int) -> set[str]:
    """Return the accepted browser Origin values for ``port``."""
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """Reject non-local hosts and cross-site state-changing requests."""

    def __init__(self, app, *, port: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.port = int(port)
        self.hosts = allowed_hosts(self.port)
        self.origins = allowed_origins(self.port)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        host = request.headers.get("host", "").strip().lower()
        if host not in self.hosts:
            return _forbidden(_DENIED_HOST)
        if request.method.upper() in MUTATING_METHODS:
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return _forbidden(_MISSING_HEADER)
            origin = request.headers.get("origin", "").strip().rstrip("/").lower()
            if origin and origin not in self.origins:
                return _forbidden(_DENIED_ORIGIN)
        return await call_next(request)


class UploadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized uploads before Starlette reads the multipart body.

    ``Content-Length`` is available before the request stream is consumed, so
    this guard runs earlier than the route-level check (which only executes
    after FastAPI has parsed every uploaded part).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method.upper() == "POST" and request.url.path == UPLOAD_PATH:
            raw_length = request.headers.get("content-length")
            if raw_length:
                # Imported lazily so tests can monkeypatch the module constant.
                from buscateses.api.routes_library import MAX_REQUEST_BYTES

                try:
                    length = int(raw_length)
                except ValueError:
                    length = 0
                if length > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        {"detail": "O envio é maior que o limite de 520 MB."},
                        status_code=413,
                    )
        return await call_next(request)


def _forbidden(message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=403)


__all__ = [
    "CSRF_HEADER",
    "CSRF_VALUE",
    "LocalSecurityMiddleware",
    "MUTATING_METHODS",
    "UPLOAD_PATH",
    "UploadSizeLimitMiddleware",
    "allowed_hosts",
    "allowed_origins",
]
