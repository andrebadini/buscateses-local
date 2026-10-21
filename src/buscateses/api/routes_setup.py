"""Initial setup and catalog update routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from buscateses.api.deps import StateDep
from buscateses.catalog.installer import CatalogError
from buscateses.embeddings.local_onnx import download_model

router = APIRouter(prefix="/api", tags=["setup"])
_COMPONENTS = {"model", "catalog"}


class SetupRequest(BaseModel):
    """Components requested by the onboarding wizard."""

    components: list[str] = Field(default_factory=lambda: ["model", "catalog"])


@router.post("/setup")
def setup(payload: SetupRequest | None = None, *, state: StateDep) -> dict[str, str]:
    """Start an exclusive setup job and return its id."""
    components = list(payload.components if payload else ["model", "catalog"])
    if not components:
        raise HTTPException(status_code=400, detail="Escolha ao menos um componente para instalar.")
    unknown = sorted(set(components) - _COMPONENTS)
    if unknown:
        raise HTTPException(status_code=400, detail="Componente de configuração desconhecido.")

    def run(ctx: Any) -> None:
        if "model" in components:
            engine = state.settings.embedding.engine
            if engine == "local":
                download_model(state.paths, state.settings.embedding.local_variant, ctx)
            elif engine in {"openai", "openai_compatible"}:
                if state.embedder is None or not state.embedder.ready():
                    raise CatalogError(
                        "Configure a URL e o nome do modelo de embedding do servidor antes de testar."
                    )
                ctx.update(
                    stage="verify",
                    message="Testando o servidor de embeddings…",
                    progress=0.5,
                )
                state.embedder.embed_queries(["BuscaTeses"])
                ctx.update(
                    stage="done",
                    message="Servidor de embeddings configurado.",
                    progress=1.0,
                )
            else:
                ctx.update(stage="done", message="Busca semântica desativada.", progress=1.0)
        if "catalog" in components:
            ctx.update(
                stage="download",
                message="Baixando o catálogo…",
                progress=0.0,
                bytes_done=0,
            )
            state.installer.install(ctx=ctx, before_activate=state.provider.close)
            state.provider.invalidate()

    try:
        job_id = state.jobs.submit("setup", run, exclusive=True)
    except Exception as exc:  # handled by the global API error layer
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@router.get("/catalog/update")
def catalog_update_info(state: StateDep) -> dict:
    """Check whether a newer catalog release is available."""
    try:
        return state.installer.check_update().to_dict()
    except CatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/catalog/update")
def catalog_update(state: StateDep) -> dict[str, str]:
    """Start an exclusive catalog update job."""
    def run(ctx: Any) -> None:
        info = state.installer.check_update(force=True)
        if not info.available:
            ctx.update(
                stage="done",
                message="O catálogo já está atualizado.",
                progress=1.0,
            )
            return
        state.installer.install(
            info.latest,
            ctx=ctx,
            before_activate=state.provider.close,
        )
        state.provider.invalidate()

    try:
        job_id = state.jobs.submit("catalog_update", run, exclusive=True)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


__all__ = ["router"]
