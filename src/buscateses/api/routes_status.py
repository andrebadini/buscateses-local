"""Status and job observation routes."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from buscateses import __version__
from buscateses.api import updates
from buscateses.api.deps import StateDep
from buscateses.jobs import JobNotFound

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def status(state: StateDep) -> dict:
    """Return app, catalog, model and library status."""
    catalog = state.catalog_status()
    model = state.model_status()
    warnings: list[str] = []
    if not catalog["installed"]:
        warnings.append("catalog_missing")
    if state.settings.embedding.engine == "local" and not model["ready"]:
        warnings.append("model_missing")
    if not model["ready"] and state.settings.embedding.engine != "none":
        if "semantic_unavailable" not in warnings:
            warnings.append("semantic_unavailable")
    setup_required = (not catalog["installed"]) or (
        state.settings.embedding.engine == "local" and not model["ready"]
    )
    return {
        "app": {"version": __version__, "pid": os.getpid()},
        "paths": {"home": str(state.paths.home)},
        "catalog": catalog,
        "model": model,
        "library": state.library_status(),
        "app_update": updates.get_status(state.settings),
        "setup_required": setup_required,
        "warnings": warnings,
    }


@router.get("/jobs")
def list_jobs(
    state: StateDep,
    active: Annotated[int | None, Query()] = None,
) -> list[dict]:
    """List recent jobs, optionally only active ones."""
    if active == 1:
        return state.jobs.list(active=True)
    if active == 0:
        return state.jobs.list(active=False)
    return state.jobs.list()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, state: StateDep) -> dict:
    """Return one job by id."""
    try:
        return state.jobs.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.") from exc


__all__ = ["router"]
