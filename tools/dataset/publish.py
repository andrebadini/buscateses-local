#!/usr/bin/env python3
"""Publish a validated release to the Hugging Face dataset repository.

The upload is guarded by a strict allowlist (only the dataset card and its
``assets/*.png`` images, the manifest and the three Parquet directories), a forbidden-column audit and a
SHA-256 check against ``manifest.json``.  ``--dry-run`` performs every local
check without touching the network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from common import is_forbidden_column, setup_logging, sha256_file

LOGGER = setup_logging("publish")
ALLOWED_ROOT_FILES = frozenset({"README.md", "manifest.json"})
ALLOWED_DIRECTORIES = frozenset({"metadata", "embeddings-256", "embeddings-768"})
#: Card images (``card.py``): PNGs are uploaded, the SVG sources stay local.
ASSETS_DIRECTORY = "assets"
ALLOWED_PATTERNS = (
    "README.md",
    "manifest.json",
    "metadata/*.parquet",
    "embeddings-256/*.parquet",
    "embeddings-768/*.parquet",
    "assets/*.png",
)
CARD_PATTERNS = ("README.md", "assets/*.png")
#: Files ``card.py`` keeps next to the release that are never uploaded.
LOCAL_ONLY_ROOT_FILES = frozenset({"card_stats.json"})
_ASSET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.png$")
_CARD_ASSET_RE = re.compile(r"/resolve/main/assets/([A-Za-z0-9._-]+)")


class PublishError(RuntimeError):
    """The release is not safe or consistent enough to publish."""


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_local_only(relative: Path) -> bool:
    if len(relative.parts) == 1:
        return relative.name in LOCAL_ONLY_ROOT_FILES
    return (
        len(relative.parts) == 2
        and relative.parts[0] == ASSETS_DIRECTORY
        and relative.suffix == ".svg"
    )


def _is_asset(relative: Path) -> bool:
    return (
        len(relative.parts) == 2
        and relative.parts[0] == ASSETS_DIRECTORY
        and bool(_ASSET_NAME_RE.match(relative.name))
    )


def _is_allowed(relative: Path) -> bool:
    if len(relative.parts) == 1:
        return relative.name in ALLOWED_ROOT_FILES
    if _is_asset(relative):
        return True
    if len(relative.parts) == 2:
        return (
            relative.parts[0] in ALLOWED_DIRECTORIES
            and relative.suffix == ".parquet"
            and relative.name.startswith("part-")
        )
    return False


def _audit_parquet_columns(path: Path) -> None:
    schema = pq.ParquetFile(path).schema_arrow
    forbidden = [name for name in schema.names if is_forbidden_column(name)]
    if forbidden:
        raise PublishError(f"{path.name}: coluna(s) proibida(s): {sorted(forbidden)}")


def _check_card_assets(release: Path, assets: set[str]) -> None:
    """Every image the card links to under ``assets/`` must be in the release."""
    readme = release / "README.md"
    if not readme.is_file():
        return
    referenced = set(_CARD_ASSET_RE.findall(readme.read_text(encoding="utf-8")))
    absent = sorted(name for name in referenced if f"{ASSETS_DIRECTORY}/{name}" not in assets)
    if absent:
        raise PublishError(
            f"O card usa imagens ausentes em assets/: {absent} (rode tools/dataset/card.py --release)"
        )


def validate_release(release: Path) -> dict[str, Any]:
    """Validate the allowlist, the manifest hashes and the Parquet schemas."""
    release = Path(release)
    if not release.is_dir():
        raise PublishError(f"Diretório de release inexistente: {release}")
    manifest_path = release / "manifest.json"
    if not manifest_path.is_file():
        raise PublishError("manifest.json ausente na release")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"manifest.json inválido: {exc}") from exc

    version = str(manifest.get("version") or "").strip()
    if not version:
        raise PublishError("manifest.json sem 'version'")

    actual_files: dict[str, Path] = {}
    for path in sorted(release.rglob("*")):
        relative = Path(_relative(path, release))
        if path.is_symlink():
            raise PublishError(f"Symlink não permitido na release: {relative.as_posix()}")
        if path.is_dir() or _is_local_only(relative):
            continue
        if not _is_allowed(relative):
            raise PublishError(f"Arquivo fora da allowlist: {relative.as_posix()}")
        actual_files[relative.as_posix()] = path

    declared = manifest.get("files")
    if not isinstance(declared, list) or not declared:
        raise PublishError("manifest.json sem lista 'files'")
    declared_paths: set[str] = set()
    for entry in declared:
        if not isinstance(entry, dict):
            raise PublishError("Entrada de 'files' deve ser um objeto")
        relative = str(entry.get("path") or "")
        if not relative:
            raise PublishError("Entrada de 'files' sem 'path'")
        if relative in declared_paths:
            raise PublishError(f"Entrada duplicada no manifesto: {relative}")
        declared_paths.add(relative)
        if not _is_allowed(Path(relative)):
            raise PublishError(f"Manifesto declara arquivo fora da allowlist: {relative}")
        path = release / relative
        if not path.is_file() or path.is_symlink():
            raise PublishError(f"Arquivo declarado no manifesto não existe: {relative}")
        expected_bytes = entry.get("bytes", entry.get("size"))
        try:
            size_matches = expected_bytes is None or path.stat().st_size == int(expected_bytes)
        except (TypeError, ValueError):
            raise PublishError(f"Tamanho inválido no manifesto para {relative}") from None
        if not size_matches:
            raise PublishError(f"Tamanho divergente para {relative}")
        expected_sha = str(entry.get("sha256") or "")
        if not expected_sha:
            raise PublishError(f"Entrada sem sha256: {relative}")
        if sha256_file(path) != expected_sha:
            raise PublishError(f"SHA-256 divergente para {relative}")

    assets = {relative for relative in actual_files if relative.startswith(f"{ASSETS_DIRECTORY}/")}
    missing = sorted(set(actual_files) - declared_paths - assets - {"README.md", "manifest.json"})
    if missing:
        raise PublishError(f"Arquivos não declarados no manifesto: {missing}")
    _check_card_assets(release, assets)

    for relative, path in actual_files.items():
        if relative.endswith(".parquet"):
            _audit_parquet_columns(path)

    return {
        "version": version,
        "files": sorted(actual_files),
        "records": manifest.get("records"),
    }


def publish_release(
    release: Path,
    repo: str,
    *,
    dry_run: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    """Validate and (unless *dry_run*) upload a release to the Hugging Face Hub."""
    release = Path(release)
    report = validate_release(release)
    LOGGER.info(
        "Release %s validada: %d arquivo(s), %s registro(s)",
        report["version"],
        len(report["files"]),
        report["records"],
    )
    if dry_run:
        LOGGER.info("--dry-run: nenhum upload será feito")
        for relative in report["files"]:
            LOGGER.info("  upload: %s", relative)
        return report

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise PublishError("Defina a variável de ambiente HF_TOKEN para publicar")

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=repo, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(
        folder_path=str(release),
        repo_id=repo,
        repo_type="dataset",
        commit_message=f"Release {report['version']}",
        allow_patterns=list(ALLOWED_PATTERNS),
    )
    api.create_tag(
        repo_id=repo,
        tag=report["version"],
        repo_type="dataset",
        exist_ok=False,
    )
    LOGGER.info("Publicado %s como release %s", repo, report["version"])
    return report


def publish_card(
    release: Path,
    repo: str,
    *,
    dry_run: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    """Upload only the dataset card (``README.md`` + ``assets/*.png``), without a new tag.

    The release is still validated in full, so the card always matches the
    published data files.
    """
    release = Path(release)
    report = validate_release(release)
    files = [
        relative
        for relative in report["files"]
        if relative == "README.md" or relative.startswith("assets/")
    ]
    if "README.md" not in files:
        raise PublishError("README.md ausente na release")
    if dry_run:
        for relative in files:
            LOGGER.info("  upload (card): %s", relative)
        return {**report, "files": files}
    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise PublishError("Defina a variável de ambiente HF_TOKEN para publicar")

    from huggingface_hub import HfApi

    HfApi(token=token).upload_folder(
        folder_path=str(release),
        repo_id=repo,
        repo_type="dataset",
        commit_message=f"Card do dataset (release {report['version']})",
        allow_patterns=list(CARD_PATTERNS),
    )
    LOGGER.info("Card publicado em %s (%d arquivo(s))", repo, len(files))
    return {**report, "files": files}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Publica a release no Hugging Face")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="ex.: andrebadini/catalogo-teses-capes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--card-only",
        action="store_true",
        help="envia só README.md e assets/*.png, sem criar tag",
    )
    arguments = parser.parse_args(argv)
    if arguments.card_only:
        publish_card(arguments.release, arguments.repo, dry_run=arguments.dry_run)
    else:
        publish_release(arguments.release, arguments.repo, dry_run=arguments.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
