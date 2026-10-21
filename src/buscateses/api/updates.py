"""Non-blocking check for new BuscaTeses releases on GitHub (SPEC §8)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests

from buscateses import __version__
from buscateses.config import Settings

_log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/andrebadini/buscateses-local/releases/latest"
REQUEST_TIMEOUT = 1.5
CACHE_SECONDS = 24 * 60 * 60
_EMPTY = {"available": False, "latest": None, "url": ""}

_lock = threading.RLock()
_cache: dict[str, Any] = dict(_EMPTY)
_checked_at: float | None = None
_thread: threading.Thread | None = None


def check_app_update(
    settings: Settings,
    *,
    force: bool = False,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Return the cached update status, querying GitHub at most once a day.

    Network failures are swallowed: callers always receive a usable dict.
    """
    global _cache, _checked_at
    if not settings.updates.check_app:
        return dict(_EMPTY)
    with _lock:
        if not force and _cache_is_fresh():
            return dict(_cache)
    result = _fetch(session)
    with _lock:
        _cache = result
        _checked_at = time.monotonic()
    return dict(result)


def get_status(settings: Settings, *, schedule: bool = True) -> dict[str, Any]:
    """Return cached update information without ever blocking on the network."""
    if not settings.updates.check_app:
        return dict(_EMPTY)
    with _lock:
        cached = dict(_cache)
        fresh = _cache_is_fresh()
    if schedule and not fresh:
        _start_background_check(settings)
    return cached


def schedule_check(settings: Settings) -> None:
    """Start a background check when updates are enabled and cache is stale."""
    if settings.updates.check_app:
        with _lock:
            fresh = _cache_is_fresh()
        if not fresh:
            _start_background_check(settings)


def reset_cache() -> None:
    """Clear the in-memory cache (used by tests)."""
    global _cache, _checked_at, _thread
    with _lock:
        _cache = dict(_EMPTY)
        _checked_at = None
        _thread = None


def _cache_is_fresh() -> bool:
    return _checked_at is not None and (time.monotonic() - _checked_at) < CACHE_SECONDS


def _start_background_check(settings: Settings) -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=check_app_update,
            args=(settings,),
            name="buscateses-update-check",
            daemon=True,
        )
        _thread.start()


def _fetch(session: requests.Session | None) -> dict[str, Any]:
    client = session or requests.Session()
    try:
        response = client.get(
            RELEASES_URL,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"BuscaTeses/{__version__}",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - offline must never break the app
        _log.debug("App update check failed", exc_info=True)
        return dict(_EMPTY)
    if not isinstance(payload, dict):
        return dict(_EMPTY)
    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    url = str(payload.get("html_url") or "").strip()
    if not latest:
        return dict(_EMPTY)
    return {
        "available": _version_tuple(latest) > _version_tuple(__version__),
        "latest": latest,
        "url": url,
    }


def _version_tuple(value: str) -> tuple[int, ...]:
    core = str(value).strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def checked_today() -> bool:
    """Return whether the cache was refreshed today (diagnostics helper)."""
    with _lock:
        if _checked_at is None:
            return False
        return datetime.now(UTC).date() == datetime.fromtimestamp(
            time.time() - (time.monotonic() - _checked_at), tz=UTC
        ).date()


__all__ = [
    "CACHE_SECONDS",
    "RELEASES_URL",
    "check_app_update",
    "checked_today",
    "get_status",
    "reset_cache",
    "schedule_check",
]
