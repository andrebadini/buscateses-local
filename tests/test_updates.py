"""Tests for the non-blocking GitHub release check."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from buscateses.api import updates


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError("http error")

    def json(self) -> Any:
        return self.payload


class _Session:
    def __init__(self, payload: Any = None, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def get(self, *args, **kwargs) -> _Response:
        self.calls += 1
        if self.fail:
            raise OSError("offline")
        return _Response(self.payload)


def _settings(settings):
    return replace(
        settings,
        updates=replace(settings.updates, check_app=True),
    )


def test_update_available_and_version_parsing(app_env) -> None:
    updates.reset_cache()
    session = _Session(
        {"tag_name": "v9.1.0", "html_url": "https://github.com/x/releases/9.1.0"}
    )
    result = updates.check_app_update(_settings(app_env["settings"]), session=session)
    assert result == {
        "available": True,
        "latest": "9.1.0",
        "url": "https://github.com/x/releases/9.1.0",
    }
    assert updates._version_tuple("v1.2.3-beta") == (1, 2, 3)
    assert updates._version_tuple("") == (0,)
    assert updates._version_tuple("2") == (2,)


def test_no_update_and_cache(app_env) -> None:
    updates.reset_cache()
    session = _Session({"tag_name": "v0.1.0", "html_url": ""})
    result = updates.check_app_update(_settings(app_env["settings"]), session=session)
    assert result["available"] is False
    assert result["latest"] == "0.1.0"
    updates.check_app_update(_settings(app_env["settings"]), session=session)
    assert session.calls == 1
    updates.check_app_update(
        _settings(app_env["settings"]), force=True, session=session
    )
    assert session.calls == 2


def test_offline_and_malformed_are_safe(app_env) -> None:
    updates.reset_cache()
    failed = updates.check_app_update(
        _settings(app_env["settings"]), session=_Session(fail=True)
    )
    assert failed == {"available": False, "latest": None, "url": ""}
    updates.reset_cache()
    malformed = updates.check_app_update(
        _settings(app_env["settings"]), session=_Session(["não é objeto"])
    )
    assert malformed["available"] is False


def test_disabled_updates_do_not_call_network(app_env) -> None:
    updates.reset_cache()
    session = _Session({"tag_name": "v9.0.0"})
    settings = replace(
        app_env["settings"], updates=replace(app_env["settings"].updates, check_app=False)
    )
    assert updates.check_app_update(settings, session=session)["latest"] is None
    assert updates.get_status(settings)["latest"] is None
    assert session.calls == 0


def test_get_status_returns_cache_without_blocking(app_env, monkeypatch) -> None:
    updates.reset_cache()
    started: list[object] = []
    monkeypatch.setattr(
        updates, "_start_background_check", lambda settings: started.append(settings)
    )
    settings = _settings(app_env["settings"])
    assert updates.get_status(settings) == {
        "available": False,
        "latest": None,
        "url": "",
    }
    assert started == [settings]
    updates.schedule_check(settings)
    assert len(started) == 2
    assert updates.checked_today() is False
