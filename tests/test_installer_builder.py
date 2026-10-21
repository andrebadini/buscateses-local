from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from filelock import FileLock

from buscateses.catalog.builder import (
    CATALOG_DB,
    FILTERS_FILE,
    FORMAT_VERSION,
    MANIFEST_FILE,
    VECTORS_FILE,
    pack_text,
    unpack_text,
)
from buscateses.catalog.installer import (
    STALE_BUILD_SECONDS,
    CatalogError,
    CatalogInstaller,
)
from buscateses.catalog.search import CatalogSearch, SearchQuery
from tests.conftest import build_dataset


def test_install_builds_release_and_cleans_downloads(installed) -> None:
    paths = installed["paths"]
    release = installed["release"]
    assert paths.current_file.is_file()
    current = json.loads(paths.current_file.read_text(encoding="utf-8"))
    assert current["version"] == installed["dataset"].version
    assert (release / CATALOG_DB).is_file()
    assert (release / VECTORS_FILE).is_file()
    assert (release / FILTERS_FILE).is_file()
    assert (release / MANIFEST_FILE).is_file()
    assert (release / "BUILD_OK").is_file()
    release_manifest = json.loads(
        (release / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    assert release_manifest["version"] == installed["dataset"].version
    vectors = np.load(release / VECTORS_FILE, mmap_mode="r")
    assert vectors.shape == (60, 256)
    assert vectors.dtype == np.float16
    filters = np.load(release / FILTERS_FILE)
    assert filters["year"].dtype == np.int16
    assert filters["degree"].dtype == np.int32
    assert filters["uf"].dtype == np.int32
    assert filters["great_area"].dtype == np.int32
    assert filters["knowledge_area"].dtype == np.int32
    assert filters["institution_acronym"].dtype == np.int32
    assert filters["degree_vocab"].size >= 2
    assert not paths.download_dir(installed["dataset"].version).exists()
    status = installed["installer"].installed_status()
    assert status["installed"] is True
    assert status["records"] == 60
    assert status["missing_vectors"] == 0
    assert status["years"] == [1990, 2024]


def test_release_uses_compact_format(installed) -> None:
    release = installed["release"]
    connection = sqlite3.connect(release / CATALOG_DB)
    try:
        meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
        columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(theses)").fetchall()
        }
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'theses_fts'"
        ).fetchone()[0]
        raw_abstract = connection.execute(
            "SELECT abstract_z FROM theses WHERE id = 'capes:0001'"
        ).fetchone()[0]
        fts_rows = connection.execute("SELECT count(*) FROM theses_fts").fetchone()[0]
    finally:
        connection.close()
    assert meta["format_version"] == str(FORMAT_VERSION)
    assert meta["vector_dtype"] == "float16"
    assert columns["abstract_z"] == "BLOB"
    assert columns["abstract_en_z"] == "BLOB"
    assert "abstract" not in columns
    assert "content=''" in schema
    assert isinstance(raw_abstract, bytes)
    assert b"Este trabalho" not in raw_abstract
    assert unpack_text(raw_abstract).startswith("Este trabalho")
    assert fts_rows == 60


def test_pack_text_round_trip() -> None:
    assert pack_text(None) is None
    assert unpack_text(None) is None
    packed = pack_text("Resumo com acentuação — educação")
    assert isinstance(packed, bytes)
    assert unpack_text(packed) == "Resumo com acentuação — educação"
    assert unpack_text("texto simples") == "texto simples"


def test_missing_vectors_counts_zero_vectors(app_env) -> None:
    dataset = app_env["dataset"]
    embeddings_path = dataset.root / "embeddings-256" / "part-000.parquet"
    table = pq.read_table(embeddings_path)
    values = table.column("embedding").to_pylist()
    values[0] = [0.0] * 256
    replacement = pa.Table.from_arrays(
        [
            table.column("id"),
            pa.array(values, type=pa.list_(pa.float16(), 256)),
        ],
        names=["id", "embedding"],
    )
    pq.write_table(replacement, embeddings_path, compression="zstd")

    manifest_path = dataset.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = embeddings_path.read_bytes()
    for item in manifest["files"]:
        if item["path"] == "embeddings-256/part-000.parquet":
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    installer.install()
    status = installer.installed_status()
    assert status["missing_vectors"] == 1
    assert status["records"] == 60


def test_update_prefers_rebuilding_legacy_format(app_env) -> None:
    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    installer.install()
    release = installer.current_release_dir()
    assert release is not None
    connection = sqlite3.connect(release / CATALOG_DB)
    try:
        connection.execute("DELETE FROM meta WHERE key = 'format_version'")
        connection.commit()
    finally:
        connection.close()
    installer.clear_update_cache()
    info = installer.check_update(force=True)
    assert info.available is True


def test_update_to_version_two_keeps_previous_release(app_env) -> None:
    paths = app_env["paths"]
    installer = CatalogInstaller(paths, app_env["settings"])
    installer.install()
    old_version = installer.current_version()
    assert old_version is not None

    updated = build_dataset(
        app_env["dataset"].root,
        version="2026.11.01",
        count=65,
        label="v2",
    )
    installer.clear_update_cache()
    info = installer.check_update(force=True)
    assert info.available is True
    assert info.latest == updated.version
    installer.install(info.latest)

    assert installer.current_version() == "2026.11.01"
    status = installer.installed_status()
    assert status["records"] == 65
    releases = {path.name for path in paths.releases_dir.iterdir() if path.is_dir()}
    assert "2026.11.01" in releases
    assert old_version in releases
    assert not any(name.startswith(".build-") for name in releases)

    catalog = CatalogSearch(paths.release_dir("2026.11.01"))
    result = catalog.search(SearchQuery(q="inclusiva", mode="text"))
    assert result.total > 0


def test_reinstall_same_version_uses_distinct_storage_dir(app_env) -> None:
    paths = app_env["paths"]
    installer = CatalogInstaller(paths, app_env["settings"])
    installer.install()
    first = installer.current_version()
    assert first == app_env["dataset"].version

    result = installer.install()
    assert result["version"] == app_env["dataset"].version
    assert result["dir"] == f"releases/{first}+f{FORMAT_VERSION}"
    assert installer.current_version() == f"{first}+f{FORMAT_VERSION}"
    assert (paths.release_dir(first) / CATALOG_DB).is_file()
    assert (paths.release_dir(f"{first}+f{FORMAT_VERSION}") / CATALOG_DB).is_file()

    installer.clear_update_cache()
    # The base version is unchanged, so no perpetual "update available".
    assert installer.check_update(force=True).available is False


def test_before_activate_sees_the_previous_catalog(app_env) -> None:
    paths = app_env["paths"]
    installer = CatalogInstaller(paths, app_env["settings"])
    installer.install()
    old_version = installer.current_version()
    old_release = installer.current_release_dir()
    assert old_version and old_release

    build_dataset(
        app_env["dataset"].root,
        version="2027.01.01",
        count=65,
        label="antes",
    )
    installer.clear_update_cache()
    seen: list[tuple[str | None, bool]] = []

    def before() -> None:
        state = installer.current_state()
        seen.append(
            (
                state["version"] if state else None,
                paths.release_dir(old_version).is_dir(),
            )
        )

    installer.install("2027.01.01", before_activate=before)
    assert seen == [(old_version, True)]
    assert installer.current_version() == "2027.01.01"
    assert old_release.is_dir()


def test_cleanup_stale_removes_unreferenced_releases(app_env) -> None:
    paths = app_env["paths"]
    installer = CatalogInstaller(paths, app_env["settings"])
    installer.install()
    current = paths.release_dir(str(installer.current_version()))
    stale = paths.release_dir("2020.01.01")
    stale.mkdir(parents=True)
    (stale / CATALOG_DB).write_bytes(b"lixo")

    installer.cleanup_stale()

    assert current.is_dir()
    assert not stale.exists()


def test_install_fails_while_another_install_holds_the_lock(app_env) -> None:
    paths = app_env["paths"].ensure()
    installer = CatalogInstaller(paths, app_env["settings"])
    lock = FileLock(str(paths.install_lock), timeout=0)
    lock.acquire()
    try:
        with pytest.raises(CatalogError, match="em andamento"):
            installer.install()
    finally:
        lock.release()


def test_cleanup_stale_keeps_recent_and_locked_build_dirs(app_env) -> None:
    paths = app_env["paths"].ensure()
    installer = CatalogInstaller(paths, app_env["settings"])
    fresh = paths.releases_dir / ".build-2026.10.21-aaaa"
    fresh.mkdir()
    old = paths.releases_dir / ".build-2020.01.01-bbbb"
    old.mkdir()
    stale_at = time.time() - STALE_BUILD_SECONDS - 60
    os.utime(old, (stale_at, stale_at))

    installer.cleanup_stale()
    assert fresh.is_dir()
    assert not old.exists()

    # While an install runs, even an old build folder must be preserved.
    busy_old = paths.releases_dir / ".build-2019.01.01-cccc"
    busy_old.mkdir()
    os.utime(busy_old, (stale_at, stale_at))
    lock = FileLock(str(paths.install_lock), timeout=0)
    lock.acquire()
    try:
        installer.cleanup_stale()
    finally:
        lock.release()
    assert busy_old.is_dir()


def test_build_failure_rolls_back(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = app_env["paths"]
    installer = CatalogInstaller(paths, app_env["settings"])
    installer.install()
    old_version = installer.current_version()
    old_release = installer.current_release_dir()
    assert old_version and old_release

    build_dataset(
        app_env["dataset"].root,
        version="2026.12.01",
        count=70,
        label="quebrada",
    )
    installer.clear_update_cache()
    info = installer.check_update(force=True)
    assert info.available

    def explode(*_args, **_kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr("buscateses.catalog.installer.build_catalog", explode)
    with pytest.raises(RuntimeError, match="falha simulada"):
        installer.install(info.latest)

    assert installer.current_version() == old_version
    assert installer.current_release_dir() == old_release
    assert not paths.release_dir("2026.12.01").exists()
    assert not any(path.name.startswith(".build-") for path in paths.releases_dir.iterdir())


def test_check_update_cache(monkeypatch: pytest.MonkeyPatch, app_env) -> None:
    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    calls = 0
    original = installer._fetch_manifest

    def counted(revision: str):
        nonlocal calls
        calls += 1
        return original(revision)

    monkeypatch.setattr(installer, "_fetch_manifest", counted)
    first = installer.check_update()
    second = installer.check_update()
    assert first.latest == second.latest
    assert calls == 1
    installer.check_update(force=True)
    assert calls == 2


def test_min_app_version_rejected(app_env) -> None:
    dataset = app_env["dataset"]
    payload = json.loads((dataset.root / "manifest.json").read_text(encoding="utf-8"))
    payload["min_app_version"] = "99.0.0"
    (dataset.root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    with pytest.raises(CatalogError, match="versão mais nova"):
        installer.check_update(force=True)


def test_manifest_required_on_disk(app_env) -> None:
    (app_env["dataset"].root / "manifest.json").unlink()
    installer = CatalogInstaller(app_env["paths"], app_env["settings"])
    with pytest.raises(CatalogError):
        installer.check_update(force=True)
