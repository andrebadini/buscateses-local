"""Serve BuscaTeses with a synthetic catalog for Playwright tests.

The app listens on 127.0.0.1:8199 and uses a throwaway data directory. A
test-only route can hide the catalog so the first-run wizard can be exercised.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Query, Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from buscateses.api import create_app  # noqa: E402
from buscateses.catalog.installer import CatalogInstaller  # noqa: E402
from buscateses.config import Paths, ServerSettings, Settings  # noqa: E402

PORT = 8199
HOST = "127.0.0.1"


def _load_test_helpers():
    """Import ``tests/conftest.py`` without importing pytest fixtures."""
    path = ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("buscateses_e2e_conftest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Não foi possível carregar os dados sintéticos de teste.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _test_router() -> APIRouter:
    """Expose a tiny hook to simulate a missing catalog."""
    router = APIRouter(prefix="/__e2e__", tags=["e2e"])
    backup_name = "current.json.e2e-bak"

    @router.post("/setup-mode")
    def setup_mode(request: Request, on: int = Query(0)) -> dict[str, bool]:
        state = request.app.state.ctx
        current = state.paths.current_file
        backup = current.with_name(backup_name)
        if on:
            if current.is_file():
                current.replace(backup)
        elif backup.is_file():
            backup.replace(current)
        state.provider.invalidate()
        return {"ok": True, "setup_required": not current.is_file()}

    @router.post("/reset")
    def reset(request: Request) -> dict[str, bool]:
        state = request.app.state.ctx
        current = state.paths.current_file
        backup = current.with_name(backup_name)
        if backup.is_file() and not current.is_file():
            backup.replace(current)
            state.provider.invalidate()
        return {"ok": True}

    return router


def main() -> None:
    helpers = _load_test_helpers()
    home = Path(tempfile.mkdtemp(prefix="buscateses-e2e-home-"))
    dataset = helpers.build_dataset(home / "dataset", count=60)
    paths = Paths.from_home(home)
    settings = Settings(
        server=ServerSettings(port=PORT),
        dataset_url=str(dataset.root),
    )
    paths.ensure()
    CatalogInstaller(paths, settings).install()

    app = create_app(
        paths,
        settings,
        embedder=helpers.FakeEmbedder(),
        extra_routers=[_test_router()],
    )
    print(f"BuscaTeses e2e em http://{HOST}:{PORT} (home {home})", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
