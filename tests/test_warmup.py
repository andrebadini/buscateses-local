"""Tests for the non-blocking startup warm-up."""

from __future__ import annotations

import threading
import time

import pytest

from buscateses.warmup import start_warmup, warm_up


def test_warm_up_calls_embedder_and_catalog() -> None:
    calls: list[str] = []

    class Embedder:
        def warm_up(self) -> None:
            calls.append("embedder")

    class Catalog:
        def warm_up(self) -> None:
            calls.append("catalog")

    warm_up(embedder=Embedder(), catalog=Catalog())
    assert calls == ["embedder", "catalog"]


def test_start_warmup_returns_immediately() -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowCatalog:
        def warm_up(self) -> None:
            started.set()
            release.wait(5)

    begin = time.perf_counter()
    thread = start_warmup(catalog=SlowCatalog())
    elapsed = time.perf_counter() - begin
    try:
        assert elapsed < 0.5
        assert started.wait(1.0)
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(1.0)
    assert not thread.is_alive()


def test_catalog_provider_runs_inside_the_worker() -> None:
    calls: list[str] = []

    class Catalog:
        def warm_up(self) -> None:
            calls.append("warm")

    thread = start_warmup(catalog_provider=Catalog)
    thread.join(2.0)
    assert calls == ["warm"]


def test_create_app_starts_warmup_in_background(
    app_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from buscateses.api import create_app

    calls: list[dict] = []
    monkeypatch.setattr(
        "buscateses.api.start_warmup", lambda **kwargs: calls.append(kwargs)
    )
    create_app(app_env["paths"], app_env["settings"])
    assert len(calls) == 1
    assert callable(calls[0]["catalog_provider"])


def test_mcp_starts_warmup_once_on_first_call(installed, monkeypatch) -> None:
    from buscateses.mcp_server import _Services

    calls: list[dict] = []
    monkeypatch.setattr(
        "buscateses.mcp_server.start_warmup", lambda **kwargs: calls.append(kwargs)
    )
    services = _Services(
        installed["paths"],
        installed["settings"],
        embedder=installed["dataset"].embedder,
    )
    assert services.catalog_search() is not None
    assert services.catalog_search() is not None
    assert len(calls) == 1
    assert calls[0]["catalog"] is not None
