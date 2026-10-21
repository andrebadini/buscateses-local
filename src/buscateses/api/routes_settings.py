"""Settings read/update and embedding diagnostics."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from buscateses.api.deps import StateDep
from buscateses.config import ConfigError, Settings, read_config_file

router = APIRouter(prefix="/api", tags=["settings"])


class TestEmbeddingRequest(BaseModel):
    """Optional probe text for the embedding test."""

    text: str | None = None


@router.get("/settings")
def get_settings_route(state: StateDep) -> dict:
    """Return settings without ever exposing API keys or MCP tokens."""
    return _public_settings(state.settings)


@router.put("/settings")
def update_settings(payload: dict[str, Any], state: StateDep) -> dict:
    """Persist only the edited fields and apply the merged settings.

    The base is the file on disk, not the in-memory settings, so runtime or
    environment values (fallback port, ``BUSCATESES_*``) are never written
    back. Empty secrets keep the previously stored value.
    """
    try:
        base = read_config_file(state.paths)
        incoming = _without_secret_markers(payload)
        merged = _deep_merge(base, incoming)
        if _embedding_url_changed(base, merged) and not _has_new_api_key(payload):
            # A stored key belongs to the previous server. Never forward it to
            # a new base URL just because the user changed the address.
            merged.setdefault("embedding", {})["api_key"] = ""
        file_settings = Settings.from_dict(merged, apply_env=False)
        file_settings.save(state.paths)
        settings = file_settings.with_env_overrides()
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.update_settings(settings)
    return _public_settings(settings)


@router.post("/settings/test-embedding")
def test_embedding(
    payload: TestEmbeddingRequest | None = None,
    *,
    state: StateDep,
) -> dict:
    """Try a small embedding to validate the configured engine."""
    embedder = state.embedder
    if embedder is None:
        return {
            "ok": False,
            "ready": False,
            "message": "A busca semântica está desativada.",
        }
    try:
        ready = bool(embedder.ready())
    except Exception:  # noqa: BLE001 - external service
        ready = False
    if not ready:
        return {
            "ok": False,
            "ready": False,
            "message": "O motor de embeddings ainda não está disponível.",
        }
    text = (payload.text if payload and payload.text else "BuscaTeses teste").strip()
    try:
        matrix = embedder.embed_queries([text])
    except Exception as exc:  # noqa: BLE001 - external service
        return {"ok": False, "ready": True, "message": str(exc)}
    dimensions = int(matrix.shape[1]) if getattr(matrix, "ndim", 0) == 2 else 0
    return {
        "ok": True,
        "ready": True,
        "dimensions": dimensions,
        "message": "O motor de embeddings respondeu corretamente.",
    }


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _embedding_url_changed(base: dict[str, Any], merged: dict[str, Any]) -> bool:
    """Return whether ``embedding.base_url`` differs from what is on disk."""
    before = base.get("embedding")
    after = merged.get("embedding")
    old_url = before.get("base_url", "") if isinstance(before, dict) else ""
    new_url = after.get("base_url", "") if isinstance(after, dict) else ""
    return str(old_url or "").strip() != str(new_url or "").strip()


def _has_new_api_key(payload: dict[str, Any]) -> bool:
    """Return whether the request explicitly supplies a non-empty API key."""
    embedding = payload.get("embedding") if isinstance(payload, dict) else None
    if not isinstance(embedding, dict):
        return False
    return bool(str(embedding.get("api_key") or "").strip())


def _public_settings(settings: Settings) -> dict[str, Any]:
    """Serialize settings and replace secrets with presence flags."""
    data = settings.to_dict()
    embedding = data.setdefault("embedding", {})
    api_key = str(embedding.pop("api_key", "") or "")
    embedding["has_api_key"] = bool(api_key.strip())
    mcp_http = data.setdefault("mcp_http", {})
    token = str(mcp_http.pop("token", "") or "")
    mcp_http["token_set"] = bool(token.strip())
    remote = data.setdefault("remote", {})
    secret = str(remote.pop("secret", "") or "")
    remote["secret_set"] = bool(secret.strip())
    return data


def _without_secret_markers(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop read-only markers and empty secret fields from a PUT payload."""
    clean = deepcopy(payload) if isinstance(payload, dict) else {}
    embedding = clean.get("embedding")
    if isinstance(embedding, dict):
        embedding.pop("has_api_key", None)
        if not str(embedding.get("api_key") or "").strip():
            embedding.pop("api_key", None)
    mcp_http = clean.get("mcp_http")
    if isinstance(mcp_http, dict):
        mcp_http.pop("token_set", None)
        if not str(mcp_http.get("token") or "").strip():
            mcp_http.pop("token", None)
    remote = clean.get("remote")
    if isinstance(remote, dict):
        remote.pop("secret_set", None)
        if not str(remote.get("secret") or "").strip():
            remote.pop("secret", None)
    return clean


__all__ = ["router"]
