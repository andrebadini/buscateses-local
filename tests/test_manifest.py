from __future__ import annotations

from copy import deepcopy

import pytest

from buscateses.catalog.manifest import ManifestError, load_manifest, parse_manifest


def _valid(dataset) -> dict:
    return deepcopy(dataset.manifest)


def test_parse_valid_manifest(dataset) -> None:
    manifest = parse_manifest(_valid(dataset))
    assert manifest.version == dataset.version
    assert manifest.records == 60
    assert manifest.embedding.app_dims == 256
    assert len(manifest.files_for_install()) == 4
    assert manifest.download_bytes == sum(
        item["bytes"] for item in dataset.manifest["files"]
    )
    assert manifest.to_dict()["years"] == dataset.manifest["years"]


def test_load_manifest(dataset) -> None:
    manifest = load_manifest(dataset.root / "manifest.json")
    assert manifest.version == dataset.version


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "metadata/../../fora.parquet",
        "metadata\\part-000.parquet",
        "outros/part-000.parquet",
        "metadata/part-000.txt",
        "metadata//part-000.parquet",
    ],
)
def test_rejects_unsafe_paths(dataset, path: str) -> None:
    payload = _valid(dataset)
    payload["files"][0]["path"] = path
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_kind_prefix_mismatch(dataset) -> None:
    payload = _valid(dataset)
    payload["files"][0]["kind"] = "embeddings-768"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_duplicate_files(dataset) -> None:
    payload = _valid(dataset)
    payload["files"].append(deepcopy(payload["files"][0]))
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_bad_sha(dataset) -> None:
    payload = _valid(dataset)
    payload["files"][0]["sha256"] = "xyz"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_bad_dimensions(dataset) -> None:
    payload = _valid(dataset)
    payload["embedding"]["app_dims"] = 128
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_unsafe_version(dataset) -> None:
    payload = _valid(dataset)
    payload["version"] = "../2026"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_missing_embeddings(dataset) -> None:
    payload = _valid(dataset)
    payload["files"] = [
        item for item in payload["files"] if item["kind"] != "embeddings-256"
    ]
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_rejects_invalid_json() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("{nao é json")
