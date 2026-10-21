from __future__ import annotations

import json
from pathlib import Path

import pytest

from buscateses.config import ConfigError, Paths, Settings, get_paths, get_settings


def test_paths_are_created_on_demand(tmp_path: Path) -> None:
    paths = get_paths(tmp_path / "data")
    assert paths.home == (tmp_path / "data").resolve()
    assert not paths.home.exists()
    paths.ensure()
    assert paths.config_file.parent.is_dir()
    assert paths.catalog_dir.is_dir()
    assert paths.releases_dir.is_dir()
    assert paths.model_dir.is_dir()
    assert paths.library_files_dir.is_dir()
    assert paths.logs_dir.is_dir()


def test_paths_reject_traversal(tmp_path: Path) -> None:
    paths = Paths.from_home(tmp_path)
    with pytest.raises(ConfigError):
        paths.release_dir("../fora")
    with pytest.raises(ConfigError):
        paths.download_dir("a/b")


def test_settings_defaults_and_roundtrip(tmp_path: Path) -> None:
    paths = Paths.from_home(tmp_path / "home")
    settings = Settings.defaults()
    assert settings.embedding.engine == "local"
    assert settings.server.port == 8101
    settings.save(paths)
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert payload["embedding"]["local_variant"] == "quantized"
    loaded = Settings.load(paths)
    assert loaded == settings
    assert not list(paths.config_file.parent.glob("*.tmp"))


def test_settings_merge_partial_and_ignore_unknown(tmp_path: Path) -> None:
    paths = Paths.from_home(tmp_path)
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"server": {"port": 9000}, "desconhecido": True}),
        encoding="utf-8",
    )
    settings = Settings.load(paths)
    assert settings.server.port == 9000
    assert settings.embedding.engine == "local"


def test_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUSCATESES_HOME", str(tmp_path / "env-home"))
    monkeypatch.setenv("BUSCATESES_PORT", "9100")
    monkeypatch.setenv("BUSCATESES_EMBED_ENGINE", "openai")
    monkeypatch.setenv("BUSCATESES_EMBED_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("BUSCATESES_EMBED_MODEL", "meu-modelo")
    monkeypatch.setenv("BUSCATESES_DATASET_REPO", "alguem/catalogo")
    monkeypatch.setenv("BUSCATESES_DATASET_URL", "https://espelho.example")
    paths = get_paths()
    settings = get_settings(paths)
    assert paths.home == (tmp_path / "env-home").resolve()
    assert settings.server.port == 9100
    assert settings.embedding.engine == "openai"
    assert settings.embedding.base_url == "http://127.0.0.1:1234/v1"
    assert settings.embedding.model == "meu-modelo"
    assert settings.dataset_repo == "alguem/catalogo"
    assert settings.dataset_url == "https://espelho.example"


def test_invalid_config_raises_friendly_error(tmp_path: Path) -> None:
    paths = Paths.from_home(tmp_path)
    paths.ensure()
    paths.config_file.write_text("{invalido", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.json"):
        Settings.load(paths)


@pytest.mark.parametrize(
    "payload",
    [
        {"server": {"port": 70000}},
        {"embedding": {"engine": "misterioso"}},
        {"embedding": {"local_variant": "gigante"}},
        {"embedding": {"base_url": "ftp://x"}},
    ],
)
def test_validation_rejects_bad_values(payload: dict) -> None:
    with pytest.raises(ConfigError):
        Settings.from_dict(payload)


def test_runtime_fields_are_not_persisted(tmp_path: Path) -> None:
    paths = Paths.from_home(tmp_path)
    settings = Settings.from_dict(
        {"dataset_repo": "outro/repo", "dataset_url": "https://exemplo"}
    )
    settings.save(paths)
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert "dataset_url" not in payload
    assert "dataset_repo" not in payload
