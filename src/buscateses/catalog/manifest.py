"""Dataset manifest model and strict validation (SPEC §3.3)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_ALLOWED_PREFIXES = {
    "metadata": "metadata/",
    "embeddings-256": "embeddings-256/",
    "embeddings-768": "embeddings-768/",
}
_FILE_KINDS = set(_ALLOWED_PREFIXES)


class ManifestError(ValueError):
    """Raised when a dataset manifest is malformed or unsafe."""


@dataclass(frozen=True)
class ManifestFile:
    """One file listed in the dataset manifest."""

    path: str
    kind: str
    rows: int
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ManifestProbe:
    """Reference query and vector head used to test remote embedders."""

    text: str
    vector_768_head: tuple[float, ...]


@dataclass(frozen=True)
class ManifestEmbedding:
    """Embedding metadata advertised by the dataset."""

    model: str
    onnx_repo: str
    app_dims: int
    full_dims: int
    document_template: str
    query_template: str
    probe: ManifestProbe


@dataclass(frozen=True)
class ManifestSource:
    """Attribution and license information."""

    name: str
    url: str
    license: str


@dataclass(frozen=True)
class Manifest:
    """Validated dataset manifest."""

    schema: int
    version: str
    created_at: str
    revision: str
    records: int
    years: tuple[int, int]
    source: ManifestSource
    embedding: ManifestEmbedding
    files: tuple[ManifestFile, ...]
    min_app_version: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Manifest:
        """Parse and validate a manifest dictionary."""
        if not isinstance(raw, dict):
            raise ManifestError("O manifest do catálogo deve ser um objeto JSON.")
        schema = _int_field(raw, "schema")
        if schema != 1:
            raise ManifestError("Versão de schema do catálogo não suportada.")

        version = _safe_name(raw, "version", "versão")
        revision = _safe_name(raw, "revision", "revisão")
        created_at = _string_field(raw, "created_at", max_length=80)
        records = _int_field(raw, "records", minimum=0)
        years = _years(raw.get("years"))
        source = _parse_source(raw.get("source"))
        embedding = _parse_embedding(raw.get("embedding"))
        files = _parse_files(raw.get("files"))
        min_app_version = _string_field(raw, "min_app_version", max_length=40)
        if not _SEMVER.fullmatch(min_app_version):
            raise ManifestError("A versão mínima do aplicativo é inválida.")

        kinds = {item.kind for item in files}
        if "metadata" not in kinds:
            raise ManifestError("O manifest não lista arquivos de metadados.")
        if "embeddings-256" not in kinds:
            raise ManifestError("O manifest não lista embeddings de 256 dimensões.")

        return cls(
            schema=schema,
            version=version,
            created_at=created_at,
            revision=revision,
            records=records,
            years=years,
            source=source,
            embedding=embedding,
            files=files,
            min_app_version=min_app_version,
        )

    def files_for_install(self) -> tuple[ManifestFile, ...]:
        """Return only the files the app downloads."""
        return tuple(
            item for item in self.files if item.kind in {"metadata", "embeddings-256"}
        )

    @property
    def download_bytes(self) -> int:
        """Total size of files downloaded by the app."""
        return sum(item.bytes for item in self.files_for_install())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest back to a JSON-compatible dictionary."""
        return {
            "schema": self.schema,
            "version": self.version,
            "created_at": self.created_at,
            "revision": self.revision,
            "records": self.records,
            "years": list(self.years),
            "source": {
                "name": self.source.name,
                "url": self.source.url,
                "license": self.source.license,
            },
            "embedding": {
                "model": self.embedding.model,
                "onnx_repo": self.embedding.onnx_repo,
                "app_dims": self.embedding.app_dims,
                "full_dims": self.embedding.full_dims,
                "document_template": self.embedding.document_template,
                "query_template": self.embedding.query_template,
                "probe": {
                    "text": self.embedding.probe.text,
                    "vector_768_head": list(self.embedding.probe.vector_768_head),
                },
            },
            "files": [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "rows": item.rows,
                    "bytes": item.bytes,
                    "sha256": item.sha256,
                }
                for item in self.files
            ],
            "min_app_version": self.min_app_version,
        }


def parse_manifest(raw: dict[str, Any] | str | bytes) -> Manifest:
    """Parse a manifest from a dictionary or JSON text."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManifestError("O manifest do catálogo não está em UTF-8.") from exc
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManifestError("O manifest do catálogo contém JSON inválido.") from exc
    return Manifest.from_dict(raw)


def load_manifest(path: str | Path) -> Manifest:
    """Read and validate a manifest file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError("Não foi possível ler o manifest do catálogo.") from exc
    return parse_manifest(text)


def _parse_source(value: Any) -> ManifestSource:
    data = _mapping(value, "source")
    return ManifestSource(
        name=_string_field(data, "name", max_length=200),
        url=_string_field(data, "url", max_length=500),
        license=_string_field(data, "license", max_length=80),
    )


def _parse_embedding(value: Any) -> ManifestEmbedding:
    data = _mapping(value, "embedding")
    probe_data = _mapping(data.get("probe"), "embedding.probe")
    app_dims = _int_field(data, "app_dims", minimum=1)
    full_dims = _int_field(data, "full_dims", minimum=1)
    if app_dims != 256 or full_dims != 768:
        raise ManifestError("O manifest exige dimensões de embedding incompatíveis.")
    probe_text = _string_field(probe_data, "text", max_length=2000)
    head_raw = probe_data.get("vector_768_head")
    if not isinstance(head_raw, (list, tuple)) or len(head_raw) < 16:
        raise ManifestError("O vetor de referência do manifest é inválido.")
    head: list[float] = []
    for value_item in head_raw[:16]:
        if isinstance(value_item, bool) or not isinstance(value_item, (int, float)):
            raise ManifestError("O vetor de referência do manifest é inválido.")
        number = float(value_item)
        if not math.isfinite(number):
            raise ManifestError("O vetor de referência do manifest é inválido.")
        head.append(number)
    return ManifestEmbedding(
        model=_string_field(data, "model", max_length=200),
        onnx_repo=_string_field(data, "onnx_repo", max_length=200),
        app_dims=app_dims,
        full_dims=full_dims,
        document_template=_string_field(data, "document_template", max_length=500),
        query_template=_string_field(data, "query_template", max_length=500),
        probe=ManifestProbe(text=probe_text, vector_768_head=tuple(head)),
    )


def _parse_files(value: Any) -> tuple[ManifestFile, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError("O manifest não lista arquivos.")
    files: list[ManifestFile] = []
    seen: set[str] = set()
    for item in value:
        data = _mapping(item, "files[]")
        path = _string_field(data, "path", max_length=500)
        _validate_file_path(path)
        kind = _string_field(data, "kind", max_length=40)
        if kind not in _FILE_KINDS:
            raise ManifestError(f"Tipo de arquivo desconhecido no manifest: {kind}")
        if not path.startswith(_ALLOWED_PREFIXES[kind]):
            raise ManifestError(f"O arquivo {path} não pertence ao tipo {kind}.")
        if path in seen:
            raise ManifestError(f"Arquivo repetido no manifest: {path}")
        seen.add(path)
        sha = _string_field(data, "sha256", max_length=64)
        if not _SHA256.fullmatch(sha):
            raise ManifestError(f"O sha256 de {path} é inválido.")
        files.append(
            ManifestFile(
                path=path,
                kind=kind,
                rows=_int_field(data, "rows", minimum=0),
                bytes=_int_field(data, "bytes", minimum=1),
                sha256=sha.lower(),
            )
        )
    return tuple(files)


def _validate_file_path(path: str) -> None:
    if "\\" in path or "//" in path or path.startswith("/") or ":" in path:
        raise ManifestError(f"Caminho de arquivo inseguro no manifest: {path}")
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ManifestError(f"Caminho de arquivo inseguro no manifest: {path}")
    if not any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES.values()):
        raise ManifestError(f"Arquivo fora das pastas permitidas: {path}")
    if candidate.suffix.lower() != ".parquet":
        raise ManifestError(f"Arquivo do catálogo deve ser .parquet: {path}")


def _years(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ManifestError("O intervalo de anos do manifest é inválido.")
    start = _as_int(value[0], "years[0]")
    end = _as_int(value[1], "years[1]")
    if start > end:
        raise ManifestError("O intervalo de anos do manifest é inválido.")
    return start, end


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"A seção {field_name} do manifest é inválida.")
    return value


def _string_field(data: dict[str, Any], field_name: str, *, max_length: int) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"O campo {field_name} do manifest é obrigatório.")
    clean = value.strip()
    if len(clean) > max_length:
        raise ManifestError(f"O campo {field_name} do manifest é muito longo.")
    return clean


def _int_field(
    data: dict[str, Any],
    field_name: str,
    *,
    minimum: int | None = None,
) -> int:
    if field_name not in data:
        raise ManifestError(f"O campo {field_name} do manifest é obrigatório.")
    result = _as_int(data[field_name], field_name)
    if minimum is not None and result < minimum:
        raise ManifestError(f"O campo {field_name} do manifest é inválido.")
    return result


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"O campo {field_name} do manifest deve ser inteiro.")
    return value


def _safe_name(data: dict[str, Any], field_name: str, label: str) -> str:
    value = _string_field(data, field_name, max_length=128)
    if not _SAFE_NAME.fullmatch(value):
        raise ManifestError(f"A {label} do catálogo é inválida.")
    return value


__all__ = [
    "Manifest",
    "ManifestEmbedding",
    "ManifestError",
    "ManifestFile",
    "ManifestProbe",
    "ManifestSource",
    "load_manifest",
    "parse_manifest",
]
