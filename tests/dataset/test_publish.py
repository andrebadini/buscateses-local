"""Tests for the release allowlist and public checks (dry-run, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import common
import publish
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

VERSION = "2026.10.21"


def _write_manifest(release: Path) -> dict[str, object]:
    files = []
    for kind in ("metadata", "embeddings-256", "embeddings-768"):
        for path in sorted((release / kind).glob("*.parquet")):
            files.append(
                {
                    "path": f"{kind}/{path.name}",
                    "kind": kind,
                    "rows": pq.ParquetFile(path).metadata.num_rows,
                    "bytes": path.stat().st_size,
                    "sha256": common.sha256_file(path),
                }
            )
    manifest = {
        "schema": 1,
        "version": VERSION,
        "revision": VERSION,
        "records": 1,
        "years": [2020, 2020],
        "files": files,
        "min_app_version": "1.0.0",
    }
    (release / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _build_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    for kind in ("metadata", "embeddings-256", "embeddings-768"):
        (release / kind).mkdir(parents=True)
    (release / "README.md").write_text("---\nlicense: cc-by-4.0\n---\n", encoding="utf-8")
    pq.write_table(pa.table({"id": ["a"], "year": pa.array([2020], pa.int16())}), release / "metadata/part-000.parquet")
    pq.write_table(pa.table({"id": ["a"]}), release / "embeddings-256/part-000.parquet")
    pq.write_table(pa.table({"id": ["a"]}), release / "embeddings-768/part-000.parquet")
    _write_manifest(release)
    return release


def test_validate_release_accepts_allowlisted_files(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    report = publish.validate_release(release)
    assert report["version"] == VERSION
    assert report["records"] == 1
    assert set(report["files"]) == {
        "README.md",
        "manifest.json",
        "metadata/part-000.parquet",
        "embeddings-256/part-000.parquet",
        "embeddings-768/part-000.parquet",
    }


@pytest.mark.parametrize("relative", ["notes.txt", "metadata/notes.txt", "metadata/part-000.parquet.bak"])
def test_validate_release_rejects_files_outside_allowlist(tmp_path: Path, relative: str) -> None:
    release = _build_release(tmp_path)
    path = release / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("não deve ser publicado", encoding="utf-8")
    with pytest.raises(publish.PublishError, match="allowlist"):
        publish.validate_release(release)


def test_validate_release_rejects_forbidden_columns(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    pq.write_table(
        pa.table({"id": ["a"], "DocumentoDiscente": ["SEGREDO-DOC"]}),
        release / "metadata/part-000.parquet",
    )
    _write_manifest(release)
    with pytest.raises(publish.PublishError, match="proibida"):
        publish.validate_release(release)


def test_validate_release_rejects_forbidden_substrings(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    pq.write_table(
        pa.table({"id": ["a"], "arquivo_pdf_path": ["x"]}),
        release / "metadata/part-000.parquet",
    )
    _write_manifest(release)
    with pytest.raises(publish.PublishError, match="proibida"):
        publish.validate_release(release)


def test_validate_release_rejects_sha_mismatch(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    target = release / "metadata/part-000.parquet"
    pq.write_table(pa.table({"id": ["a"], "year": pa.array([2021], pa.int16())}), target)
    with pytest.raises(publish.PublishError, match="SHA-256"):
        publish.validate_release(release)


def test_validate_release_rejects_undeclared_file(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    (release / "metadata/part-001.parquet").write_bytes(b"extra")
    with pytest.raises(publish.PublishError, match="não declarados"):
        publish.validate_release(release)


def test_validate_release_rejects_symlinks(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    link = release / "metadata" / "part-001.parquet"
    link.symlink_to(release / "metadata" / "part-000.parquet")
    with pytest.raises(publish.PublishError, match="Symlink"):
        publish.validate_release(release)


def test_dry_run_needs_no_token_and_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    release = _build_release(tmp_path)
    report = publish.publish_release(release, "andrebadini/catalogo-teses-capes", dry_run=True)
    assert report["version"] == VERSION
    assert len(report["files"]) == 5


def test_validate_release_accepts_card_assets_and_ignores_local_files(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    assets = release / "assets"
    assets.mkdir()
    (assets / "anos.pt.png").write_bytes(b"\x89PNG")
    (assets / "anos.pt.svg").write_text("<svg/>", encoding="utf-8")
    (release / "card_stats.json").write_text("{}", encoding="utf-8")
    (release / "README.md").write_text(
        "---\nlicense: cc-by-4.0\n---\n"
        '<img src="https://huggingface.co/datasets/x/y/resolve/main/assets/anos.pt.png">\n',
        encoding="utf-8",
    )
    report = publish.validate_release(release)
    assert "assets/anos.pt.png" in report["files"]
    assert "assets/anos.pt.svg" not in report["files"]
    assert "card_stats.json" not in report["files"]


def test_validate_release_rejects_missing_card_image(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    (release / "README.md").write_text(
        '<img src="https://huggingface.co/datasets/x/y/resolve/main/assets/anos.pt.png">\n',
        encoding="utf-8",
    )
    with pytest.raises(publish.PublishError, match="imagens ausentes"):
        publish.validate_release(release)


def test_validate_release_rejects_unexpected_asset(tmp_path: Path) -> None:
    release = _build_release(tmp_path)
    (release / "assets").mkdir()
    (release / "assets" / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(publish.PublishError, match="allowlist"):
        publish.validate_release(release)


def test_publish_card_dry_run_lists_only_card_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    release = _build_release(tmp_path)
    (release / "assets").mkdir()
    (release / "assets" / "banner.pt.png").write_bytes(b"\x89PNG")
    report = publish.publish_card(release, "andrebadini/catalogo-teses-capes", dry_run=True)
    assert report["files"] == ["README.md", "assets/banner.pt.png"]
