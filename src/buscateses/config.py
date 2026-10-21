"""User data paths and application settings.

The module deliberately avoids module-level singletons: every loader accepts an
explicit home directory, which keeps tests isolated and makes concurrent users
of the library predictable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
_SAFE_SECRET = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_EMBED_ENGINES = {"local", "openai", "openai_compatible", "none"}


class ConfigError(ValueError):
    """Raised when configuration or a data path cannot be used."""


def _resolve_home(home: str | Path | None = None) -> Path:
    raw = home if home is not None else os.getenv("BUSCATESES_HOME")
    if raw is None or str(raw).strip() == "":
        return Path(user_data_path("BuscaTeses", appauthor=False)).expanduser().resolve()
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Paths:
    """All files and directories used by BuscaTeses (SPEC §2)."""

    home: Path

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> Paths:
        """Build paths from an explicit home or ``BUSCATESES_HOME``."""
        return cls(home=_resolve_home(home))

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    @property
    def catalog_dir(self) -> Path:
        return self.home / "catalog"

    @property
    def current_file(self) -> Path:
        return self.catalog_dir / "current.json"

    @property
    def releases_dir(self) -> Path:
        return self.catalog_dir / "releases"

    @property
    def downloads_dir(self) -> Path:
        return self.catalog_dir / "downloads"

    @property
    def models_dir(self) -> Path:
        return self.home / "models"

    @property
    def model_dir(self) -> Path:
        return self.models_dir / "embeddinggemma-300m"

    @property
    def library_dir(self) -> Path:
        return self.home / "library"

    @property
    def library_db(self) -> Path:
        return self.library_dir / "library.sqlite"

    @property
    def library_vectors(self) -> Path:
        return self.library_dir / "vectors.npy"

    @property
    def library_vector_ids(self) -> Path:
        return self.library_dir / "vector_rowids.npy"

    @property
    def library_files_dir(self) -> Path:
        return self.library_dir / "files"

    @property
    def user_db(self) -> Path:
        return self.home / "user.sqlite"

    @property
    def bin_dir(self) -> Path:
        """Directory for optional helper executables such as cloudflared."""
        return self.home / "bin"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "buscateses.log"

    @property
    def runtime_file(self) -> Path:
        return self.home / "runtime.json"

    @property
    def remote_runtime_file(self) -> Path:
        """Runtime information published by ``buscateses remote start``."""
        return self.home / "remote.json"

    @property
    def app_lock(self) -> Path:
        """Cross-process lock that keeps a single desktop/server instance."""
        return self.home / "app.lock"

    @property
    def library_lock(self) -> Path:
        """Cross-process lock that serialises library writes."""
        return self.library_dir / ".write.lock"

    @property
    def install_lock(self) -> Path:
        """Cross-process lock that serialises catalog installs and updates."""
        return self.catalog_dir / ".install.lock"

    def ensure(self) -> Paths:
        """Create all data directories on demand and return ``self``."""
        for directory in (
            self.home,
            self.catalog_dir,
            self.releases_dir,
            self.downloads_dir,
            self.models_dir,
            self.model_dir,
            self.library_dir,
            self.library_files_dir,
            self.bin_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def release_dir(self, version: str) -> Path:
        """Return a safe release directory for ``version``."""
        clean = str(version).strip()
        if not _SAFE_VERSION.fullmatch(clean):
            raise ConfigError("Versão de catálogo inválida.")
        return self.releases_dir / clean

    def download_dir(self, version: str) -> Path:
        """Return a safe download directory for ``version``."""
        clean = str(version).strip()
        if not _SAFE_VERSION.fullmatch(clean):
            raise ConfigError("Versão de catálogo inválida.")
        return self.downloads_dir / clean


def get_paths(home: str | Path | None = None) -> Paths:
    """Return paths for ``home`` (or the platform default)."""
    return Paths.from_home(home)


def read_config_file(paths: Paths) -> dict[str, Any]:
    """Read the raw ``config.json`` object, or an empty dict when missing."""
    config_file = paths.config_file
    if not config_file.exists():
        return {}
    try:
        with config_file.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Não foi possível ler {config_file.name}. Corrija o arquivo ou apague-o."
        ) from exc
    if not isinstance(loaded, dict):
        raise ConfigError("O arquivo config.json deve conter um objeto JSON.")
    return loaded


@dataclass(frozen=True)
class EmbeddingSettings:
    """Embedding engine configuration."""

    engine: str = "local"
    local_variant: str = "quantized"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class ServerSettings:
    """Local web server configuration."""

    port: int = 8101


@dataclass(frozen=True)
class UpdateSettings:
    """Automatic update checks."""

    check_catalog: bool = True
    check_app: bool = True


@dataclass(frozen=True)
class McpHttpSettings:
    """Optional Streamable HTTP MCP endpoint."""

    enabled: bool = False
    port: int = 8102
    token: str = ""
    #: Extra public host names accepted behind an HTTPS tunnel.
    allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteSettings:
    """Public MCP address created through a Cloudflare Quick Tunnel."""

    secret: str = ""
    include_library: bool = False


@dataclass(frozen=True)
class Settings:
    """Validated application settings persisted in ``config.json``."""

    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    updates: UpdateSettings = field(default_factory=UpdateSettings)
    mcp_http: McpHttpSettings = field(default_factory=McpHttpSettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)
    onboarding_done: bool = False
    dataset_repo: str = "andrebadini/catalogo-teses-capes"
    dataset_url: str = ""

    @classmethod
    def defaults(cls) -> Settings:
        """Return settings with all documented defaults."""
        return cls()

    @classmethod
    def load(
        cls,
        paths: Paths | None = None,
        *,
        home: str | Path | None = None,
    ) -> Settings:
        """Load settings, merging the file over defaults and applying env vars."""
        resolved_paths = paths or get_paths(home)
        return cls.from_dict(read_config_file(resolved_paths))

    @classmethod
    def load_file(
        cls,
        paths: Paths | None = None,
        *,
        home: str | Path | None = None,
    ) -> Settings:
        """Load only what is persisted on disk, ignoring environment overrides."""
        resolved_paths = paths or get_paths(home)
        return cls.from_dict(read_config_file(resolved_paths), apply_env=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, apply_env: bool = True) -> Settings:
        """Build validated settings from a (possibly partial) dictionary."""
        if not isinstance(raw, dict):
            raise ConfigError("A configuração deve ser um objeto JSON.")

        defaults = cls()
        embedding_raw = _mapping(raw.get("embedding"))
        server_raw = _mapping(raw.get("server"))
        updates_raw = _mapping(raw.get("updates"))
        mcp_raw = _mapping(raw.get("mcp_http"))
        remote_raw = _mapping(raw.get("remote"))

        embedding = EmbeddingSettings(
            engine=str(embedding_raw.get("engine", defaults.embedding.engine)).strip(),
            local_variant=str(
                embedding_raw.get("local_variant", defaults.embedding.local_variant)
            ).strip(),
            base_url=str(embedding_raw.get("base_url", defaults.embedding.base_url)).strip(),
            model=str(embedding_raw.get("model", defaults.embedding.model)).strip(),
            api_key=str(embedding_raw.get("api_key", defaults.embedding.api_key)),
        )
        server = ServerSettings(port=_as_int(server_raw.get("port"), defaults.server.port, "porta"))
        updates = UpdateSettings(
            check_catalog=_as_bool(
                updates_raw.get("check_catalog"), defaults.updates.check_catalog
            ),
            check_app=_as_bool(updates_raw.get("check_app"), defaults.updates.check_app),
        )
        mcp_http = McpHttpSettings(
            enabled=_as_bool(mcp_raw.get("enabled"), defaults.mcp_http.enabled),
            port=_as_int(mcp_raw.get("port"), defaults.mcp_http.port, "porta do MCP"),
            token=str(mcp_raw.get("token", defaults.mcp_http.token)),
            allowed_hosts=normalize_allowed_hosts(
                mcp_raw.get("allowed_hosts", defaults.mcp_http.allowed_hosts)
            ),
        )
        remote = RemoteSettings(
            secret=str(remote_raw.get("secret", defaults.remote.secret)).strip(),
            include_library=_as_bool(
                remote_raw.get("include_library"), defaults.remote.include_library
            ),
        )
        settings = cls(
            embedding=embedding,
            server=server,
            updates=updates,
            mcp_http=mcp_http,
            remote=remote,
            onboarding_done=_as_bool(raw.get("onboarding_done"), defaults.onboarding_done),
            dataset_repo=str(raw.get("dataset_repo", defaults.dataset_repo)).strip(),
            dataset_url=str(raw.get("dataset_url", defaults.dataset_url)).strip(),
        )
        return settings.with_env_overrides() if apply_env else settings

    def with_env_overrides(self) -> Settings:
        """Return a copy with documented environment variables applied."""
        overrides: dict[str, Any] = {}
        if (value := os.getenv("BUSCATESES_PORT")) is not None:
            overrides["server"] = ServerSettings(
                port=_as_int(value, self.server.port, "porta")
            )
        if (value := os.getenv("BUSCATESES_EMBED_ENGINE")) is not None:
            overrides["embedding"] = EmbeddingSettings(
                engine=value.strip() or self.embedding.engine,
                local_variant=self.embedding.local_variant,
                base_url=self.embedding.base_url,
                model=self.embedding.model,
                api_key=self.embedding.api_key,
            )
        if (value := os.getenv("BUSCATESES_EMBED_URL")) is not None:
            current = overrides.get("embedding", self.embedding)
            overrides["embedding"] = EmbeddingSettings(
                engine=current.engine,
                local_variant=current.local_variant,
                base_url=value.strip(),
                model=current.model,
                api_key=current.api_key,
            )
        if (value := os.getenv("BUSCATESES_EMBED_MODEL")) is not None:
            current = overrides.get("embedding", self.embedding)
            overrides["embedding"] = EmbeddingSettings(
                engine=current.engine,
                local_variant=current.local_variant,
                base_url=current.base_url,
                model=value.strip(),
                api_key=current.api_key,
            )
        if (value := os.getenv("BUSCATESES_DATASET_REPO")) is not None:
            overrides["dataset_repo"] = value.strip() or self.dataset_repo
        if (value := os.getenv("BUSCATESES_DATASET_URL")) is not None:
            overrides["dataset_url"] = value.strip()

        result = self
        if overrides:
            from dataclasses import replace

            result = replace(self, **overrides)
        return result.validate()

    def validate(self) -> Settings:
        """Validate all fields, raising :class:`ConfigError` when invalid."""
        if self.embedding.engine not in _EMBED_ENGINES:
            raise ConfigError(
                "Motor de embeddings inválido. Use 'local', 'openai' ou 'none'."
            )
        if self.embedding.local_variant not in {"quantized", "fp32"}:
            raise ConfigError(
                "Variante do modelo de embedding local inválida. "
                "Use 'quantized' ou 'fp32'."
            )
        if not 1 <= self.server.port <= 65535:
            raise ConfigError("A porta do servidor deve estar entre 1 e 65535.")
        if not 1 <= self.mcp_http.port <= 65535:
            raise ConfigError("A porta do MCP deve estar entre 1 e 65535.")
        for host in self.mcp_http.allowed_hosts:
            _validate_allowed_host(host)
        if self.remote.secret and not _SAFE_SECRET.fullmatch(self.remote.secret):
            raise ConfigError(
                "O segredo do endereço público é inválido. Gere um novo endereço."
            )
        if self.embedding.base_url and not self.embedding.base_url.startswith(
            ("http://", "https://")
        ):
            raise ConfigError("A URL do servidor de embeddings deve começar com http:// ou https://.")
        if not self.dataset_repo or any(char.isspace() for char in self.dataset_repo):
            raise ConfigError("O identificador do dataset é inválido.")
        return self

    def to_dict(self, *, include_runtime: bool = False) -> dict[str, Any]:
        """Serialize settings to the documented config.json structure."""
        data: dict[str, Any] = {
            "embedding": {
                "engine": self.embedding.engine,
                "local_variant": self.embedding.local_variant,
                "base_url": self.embedding.base_url,
                "model": self.embedding.model,
                "api_key": self.embedding.api_key,
            },
            "server": {"port": self.server.port},
            "updates": {
                "check_catalog": self.updates.check_catalog,
                "check_app": self.updates.check_app,
            },
            "mcp_http": {
                "enabled": self.mcp_http.enabled,
                "port": self.mcp_http.port,
                "token": self.mcp_http.token,
                "allowed_hosts": list(self.mcp_http.allowed_hosts),
            },
            "remote": {
                "secret": self.remote.secret,
                "include_library": self.remote.include_library,
            },
            "onboarding_done": self.onboarding_done,
        }
        if include_runtime:
            data["dataset_repo"] = self.dataset_repo
            data["dataset_url"] = self.dataset_url
        return data

    def save(self, paths: Paths | None = None, *, home: str | Path | None = None) -> Path:
        """Atomically write settings to ``config.json`` and return its path."""
        resolved_paths = paths or get_paths(home)
        resolved_paths.ensure()
        destination = resolved_paths.config_file
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return destination


def get_settings(
    paths: Paths | None = None,
    *,
    home: str | Path | None = None,
) -> Settings:
    """Load settings without hidden global caching."""
    return Settings.load(paths, home=home)


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("Uma seção da configuração deve ser um objeto JSON.")
    return value


def _as_int(value: Any, default: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"Valor inválido para {label}.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Valor inválido para {label}.") from exc


def normalize_allowed_hosts(value: Any) -> tuple[str, ...]:
    """Normalise the ``mcp_http.allowed_hosts`` config value."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ConfigError("A lista de hosts do MCP deve ser uma lista de textos.")
    hosts: list[str] = []
    for item in items:
        host = str(item).strip().lower()
        if not host:
            continue
        _validate_allowed_host(host)
        if host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _validate_allowed_host(host: str) -> None:
    """Reject values that are not a bare host name with an optional port."""
    candidate = str(host or "").strip().lower()
    if (
        not candidate
        or "://" in candidate
        or "/" in candidate
        or "*" in candidate
        or any(char.isspace() for char in candidate)
    ):
        raise ConfigError(
            "Host do MCP inválido. Informe apenas o domínio, como "
            "exemplo.ts.net (sem https://)."
        )
    name, separator, port = candidate.rpartition(":")
    if separator:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ConfigError("Porta inválida no host do MCP.")


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "sim", "on"}:
            return True
        if lowered in {"0", "false", "no", "nao", "não", "off"}:
            return False
    raise ConfigError("Valor booleano inválido na configuração.")
