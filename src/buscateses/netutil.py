"""Local TCP socket helpers shared by the desktop app and the CLI."""

from __future__ import annotations

import socket
import sys

LOOPBACK = "127.0.0.1"
DEFAULT_ATTEMPTS = 100


def _new_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        # Avoid the classic Windows hijack where another process binds the
        # same port after the check but before uvicorn starts listening.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    return sock


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind ``host:port`` without ``SO_REUSEADDR`` and return the socket.

    The caller owns the returned socket and must close it (or hand it to
    uvicorn). Raises :class:`OSError` when the address is unavailable.
    """
    sock = _new_socket()
    try:
        sock.bind((host, int(port)))
    except BaseException:
        sock.close()
        raise
    return sock


def try_bind(host: str, port: int) -> socket.socket | None:
    """Bind ``host:port`` or return ``None`` when it is already taken."""
    try:
        return bind_socket(host, port)
    except OSError:
        return None


def find_free_socket(
    start: int,
    *,
    host: str = LOOPBACK,
    attempts: int = DEFAULT_ATTEMPTS,
) -> socket.socket:
    """Return a bound socket on the first free port at or after ``start``."""
    first = max(1, min(int(start), 65535))
    last = min(65535, first + max(1, attempts) - 1)
    for port in range(first, last + 1):
        sock = try_bind(host, port)
        if sock is not None:
            return sock
    raise OSError("Não há portas livres para iniciar o BuscaTeses.")


def find_free_port(
    start: int,
    *,
    host: str = LOOPBACK,
    attempts: int = DEFAULT_ATTEMPTS,
) -> int:
    """Return the first free port at or after ``start`` (socket closed after use)."""
    sock = find_free_socket(start, host=host, attempts=attempts)
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def port_in_use(host: str, port: int) -> bool:
    """Return whether ``host:port`` cannot be bound right now."""
    sock = try_bind(host, port)
    if sock is None:
        return True
    sock.close()
    return False


__all__ = [
    "DEFAULT_ATTEMPTS",
    "LOOPBACK",
    "bind_socket",
    "find_free_port",
    "find_free_socket",
    "port_in_use",
    "try_bind",
]
