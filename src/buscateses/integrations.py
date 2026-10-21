"""Automatic MCP configuration for AI assistant clients (SPEC §10)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from buscateses.config import get_paths

TOM_BLOCK = "mcp_servers.buscateses"
_BACKUP_SUFFIX = ".bak-buscateses"
_RESTART_HINT = "Reinicie o cliente para que ele carregue o BuscaTeses."
_SUBPROCESS_TIMEOUT = 20


class IntegrationError(RuntimeError):
    """Raised when a client configuration cannot be read or changed."""


@dataclass(frozen=True)
class ClientSpec:
    """Static description of one supported assistant client."""

    id: str
    name: str
    kind: str
    format: str


@dataclass
class IntegrationResult:
    """Outcome of a connect/disconnect operation."""

    id: str
    connected: bool
    changed: bool
    message: str
    config_path: str | None
    kind: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON representation used by the HTTP API."""
        return {
            "id": self.id,
            "connected": self.connected,
            "changed": self.changed,
            "message": self.message,
            "config_path": self.config_path,
            "kind": self.kind,
        }


CLIENTS: tuple[ClientSpec, ...] = (
    ClientSpec("claude_desktop", "Claude Desktop", "auto", "json"),
    ClientSpec("cursor", "Cursor", "auto", "json"),
    ClientSpec("lm_studio", "LM Studio", "auto", "json"),
    ClientSpec("gemini_cli", "Gemini CLI", "auto", "json"),
    ClientSpec("codex", "Codex CLI", "auto", "toml"),
    ClientSpec("claude_code", "Claude Code", "command", "command"),
    ClientSpec("vscode", "VS Code", "manual", "manual"),
)
_CLIENTS_BY_ID = {client.id: client for client in CLIENTS}
_INSTALL_URLS = {
    "claude_desktop": "https://claude.ai/download",
    "cursor": "https://www.cursor.com/downloads",
    "lm_studio": "https://lmstudio.ai/",
    "gemini_cli": "https://github.com/google-gemini/gemini-cli",
    "codex": "https://github.com/openai/codex",
    "claude_code": "https://www.anthropic.com/claude-code",
    "vscode": "https://code.visualstudio.com/download",
}


def mcp_command() -> tuple[str, list[str]]:
    """Return the command and arguments clients should launch."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # macOS and Windows use case-insensitive file systems, so a console
        # executable named "buscateses" would collide with "BuscaTeses".
        # The packaged builds therefore call it "buscateses-cli" there.
        if sys.platform == "win32":
            names = ("buscateses-cli.exe",)
        elif sys.platform == "darwin":
            names = ("buscateses-cli",)
        else:
            names = ("buscateses", "buscateses-cli")
        for name in names:
            console = executable.parent / name
            if console.is_file():
                return str(console), ["mcp"]
        return str(executable), ["mcp"]
    return sys.executable, ["-m", "buscateses", "mcp"]


def mcp_env(data_home: str | Path | None = None) -> dict[str, str]:
    """Return the environment clients need to reach a non-default data folder."""
    if data_home is None:
        resolved = get_paths().home
    else:
        resolved = Path(data_home).expanduser().resolve()
    default = Path(user_data_path("BuscaTeses", appauthor=False)).expanduser().resolve()
    if resolved == default:
        return {}
    return {"BUSCATESES_HOME": str(resolved)}


def list_integrations(
    home: str | Path | None = None,
    *,
    data_home: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Describe every supported client and its current connection state."""
    base = _home(home)
    command, args = mcp_command()
    env = mcp_env(data_home)
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "kind": spec.kind,
            "detected": _detected(spec, base),
            "connected": _connected(spec, base),
            "config_path": _config_path(spec, base),
            "snippet": _snippet(spec, command, args, env),
            "instructions": _instructions(spec, command, args),
            "install_url": _INSTALL_URLS.get(spec.id),
        }
        for spec in CLIENTS
    ]


def connect(
    client_id: str,
    home: str | Path | None = None,
    *,
    data_home: str | Path | None = None,
) -> IntegrationResult:
    """Configure ``client_id`` to launch the BuscaTeses MCP server."""
    spec = _spec(client_id)
    base = _home(home)
    command, args = mcp_command()
    env = mcp_env(data_home)
    if spec.format == "json":
        return _connect_json(spec, base, command, args, env)
    if spec.format == "toml":
        return _connect_toml(spec, base, command, args, env)
    if spec.format == "command":
        return _connect_claude_code(spec, base, command, args, env)
    return IntegrationResult(
        id=spec.id,
        connected=False,
        changed=False,
        message=_instructions(spec, command, args),
        config_path=None,
        kind=spec.kind,
    )


def disconnect(client_id: str, home: str | Path | None = None) -> IntegrationResult:
    """Remove the BuscaTeses entry from ``client_id`` without touching others."""
    spec = _spec(client_id)
    base = _home(home)
    if spec.format == "json":
        return _disconnect_json(spec, base)
    if spec.format == "toml":
        return _disconnect_toml(spec, base)
    if spec.format == "command":
        return _disconnect_claude_code(spec)
    return IntegrationResult(
        id=spec.id,
        connected=False,
        changed=False,
        message="Este cliente usa configuração manual; nada foi alterado.",
        config_path=None,
        kind=spec.kind,
    )


def _spec(client_id: str) -> ClientSpec:
    clean = str(client_id or "").strip()
    spec = _CLIENTS_BY_ID.get(clean)
    if spec is None:
        raise IntegrationError("Cliente de IA desconhecido.")
    return spec


def _home(home: str | Path | None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _config_path(spec: ClientSpec, home: Path) -> str | None:
    if spec.id == "claude_desktop":
        if sys.platform == "darwin":
            path = home / "Library/Application Support/Claude/claude_desktop_config.json"
        elif sys.platform.startswith("win"):
            appdata = Path(os.environ.get("APPDATA") or home / "AppData/Roaming")
            path = appdata / "Claude/claude_desktop_config.json"
        else:
            path = home / ".config/Claude/claude_desktop_config.json"
        return str(path)
    if spec.id == "cursor":
        return str(home / ".cursor/mcp.json")
    if spec.id == "lm_studio":
        return str(home / ".lmstudio/mcp.json")
    if spec.id == "gemini_cli":
        return str(home / ".gemini/settings.json")
    if spec.id == "codex":
        return str(home / ".codex/config.toml")
    return None


def _detected(spec: ClientSpec, home: Path) -> bool:
    if spec.id == "claude_code":
        return shutil.which("claude") is not None
    if spec.id == "vscode":
        return shutil.which("code") is not None or (home / ".vscode").exists()
    if spec.kind == "manual":
        return False
    path = _config_path(spec, home)
    if path is None:
        return False
    candidate = Path(path)
    return candidate.is_file() or candidate.parent.exists()


def _connected(spec: ClientSpec, home: Path) -> bool:
    if spec.format == "json":
        path = Path(_config_path(spec, home) or "")
        try:
            data = _load_json(path, allow_missing=True)
        except IntegrationError:
            return False
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        return isinstance(servers, dict) and isinstance(servers.get("buscateses"), dict)
    if spec.format == "toml":
        path = Path(_config_path(spec, home) or "")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        return _toml_has_entry(text)
    if spec.id == "claude_code":
        for candidate in (home / ".claude.json", home / ".claude/settings.json"):
            try:
                data = _load_json(candidate, allow_missing=True)
            except IntegrationError:
                continue
            servers = data.get("mcpServers")
            if isinstance(servers, dict) and "buscateses" in servers:
                return True
        return False
    return False


def _snippet(
    spec: ClientSpec,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> str:
    environment = dict(env or {})
    if spec.format == "json":
        entry: dict[str, Any] = {"command": command, "args": list(args)}
        if environment:
            entry["env"] = environment
        return json.dumps(
            {"mcpServers": {"buscateses": entry}},
            ensure_ascii=False,
            indent=2,
        )
    if spec.format == "toml":
        entry = {"command": command, "args": list(args)}
        if environment:
            entry["env"] = environment
        return "\n".join(_toml_lines(entry))
    if spec.id == "claude_code":
        return " ".join(
            ["claude", *_claude_add_args(command, args, environment)]
        )
    if spec.id == "vscode":
        entry = {
            "type": "stdio",
            "command": command,
            "args": list(args),
        }
        if environment:
            entry["env"] = environment
        payload = {"servers": {"buscateses": entry}}
        return json.dumps(payload, ensure_ascii=False, indent=2)
    entry = {"command": command, "args": list(args)}
    if environment:
        entry["env"] = environment
    return json.dumps({"mcpServers": {"buscateses": entry}}, ensure_ascii=False, indent=2)


def _instructions(spec: ClientSpec, command: str, args: list[str]) -> str:
    del command, args
    if spec.kind == "manual":
        return (
            "Este aplicativo usa configuração manual: abra as configurações de MCP "
            "e cole o trecho exibido. Depois reinicie o aplicativo."
        )
    if spec.id == "claude_code":
        return (
            "O BuscaTeses será registrado no escopo de usuário do Claude Code. "
            "Reinicie o Claude Code para carregar o servidor."
        )
    return f"Conectado. {_RESTART_HINT}"


# -- JSON clients ---------------------------------------------------------------


def _connect_json(
    spec: ClientSpec,
    home: Path,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> IntegrationResult:
    path = Path(_config_path(spec, home) or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_json(path, allow_missing=True)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise IntegrationError(
            "A seção mcpServers deste arquivo não é um objeto JSON válido."
        )
    previous = servers.get("buscateses")
    entry = dict(previous) if isinstance(previous, dict) else {}
    entry["command"] = command
    entry["args"] = list(args)
    _merge_entry_env(entry, env)
    if previous == entry:
        return IntegrationResult(
            id=spec.id,
            connected=True,
            changed=False,
            message=f"O {spec.name} já está conectado. {_RESTART_HINT}",
            config_path=str(path),
            kind=spec.kind,
        )
    _backup(path)
    servers["buscateses"] = entry
    data["mcpServers"] = servers
    _write_json(path, data)
    return IntegrationResult(
        id=spec.id,
        connected=True,
        changed=True,
        message=f"Conectado ao {spec.name}. {_RESTART_HINT}",
        config_path=str(path),
        kind=spec.kind,
    )


def _disconnect_json(spec: ClientSpec, home: Path) -> IntegrationResult:
    path = Path(_config_path(spec, home) or "")
    if not path.is_file():
        return IntegrationResult(
            id=spec.id,
            connected=False,
            changed=False,
            message=f"O {spec.name} já estava desconectado.",
            config_path=str(path),
            kind=spec.kind,
        )
    data = _load_json(path, allow_missing=False)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "buscateses" not in servers:
        return IntegrationResult(
            id=spec.id,
            connected=False,
            changed=False,
            message=f"O {spec.name} já estava desconectado.",
            config_path=str(path),
            kind=spec.kind,
        )
    _backup(path)
    del servers["buscateses"]
    data["mcpServers"] = servers
    _write_json(path, data)
    return IntegrationResult(
        id=spec.id,
        connected=False,
        changed=True,
        message=f"Desconectado do {spec.name}. {_RESTART_HINT}",
        config_path=str(path),
        kind=spec.kind,
    )


def _load_json(path: Path, *, allow_missing: bool) -> dict[str, Any]:
    if not path.is_file():
        if allow_missing:
            return {}
        raise IntegrationError("O arquivo de configuração não foi encontrado.")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            f"O arquivo {path} não é um JSON válido. Comentários no estilo JSONC "
            "não são aceitos. Corrija-o antes de conectar."
        ) from exc
    except OSError as exc:
        raise IntegrationError(
            f"Não foi possível ler {path}. Verifique as permissões do arquivo."
        ) from exc
    if not isinstance(data, dict):
        raise IntegrationError(f"O arquivo {path} deve conter um objeto JSON.")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


# -- Codex TOML -----------------------------------------------------------------

_TOML_TARGET = ("mcp_servers", "buscateses")
_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _connect_toml(
    spec: ClientSpec,
    home: Path,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> IntegrationResult:
    path = Path(_config_path(spec, home) or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    document = _parse_toml(text, path)
    existing = _lookup(document, _TOML_TARGET)
    entry = _merge_toml_entry(existing, command, args, env)
    if isinstance(existing, dict) and existing == entry:
        return IntegrationResult(
            id=spec.id,
            connected=True,
            changed=False,
            message=f"O {spec.name} já está conectado. {_RESTART_HINT}",
            config_path=str(path),
            kind=spec.kind,
        )
    updated, changed = _upsert_toml_block(text, entry)
    if not changed:
        return IntegrationResult(
            id=spec.id,
            connected=True,
            changed=False,
            message=f"O {spec.name} já está conectado. {_RESTART_HINT}",
            config_path=str(path),
            kind=spec.kind,
        )
    _validate_toml(updated, path)
    if _lookup(_parse_toml(updated, path), _TOML_TARGET) != entry:
        raise IntegrationError(
            "Não foi possível atualizar a configuração do Codex sem alterar outras "
            "entradas. Edite o arquivo manualmente."
        )
    _backup(path if path.is_file() else None)
    _atomic_write(path, updated)
    return IntegrationResult(
        id=spec.id,
        connected=True,
        changed=True,
        message=f"Conectado ao {spec.name}. {_RESTART_HINT}",
        config_path=str(path),
        kind=spec.kind,
    )


def _disconnect_toml(spec: ClientSpec, home: Path) -> IntegrationResult:
    path = Path(_config_path(spec, home) or "")
    result_kwargs = {
        "id": spec.id,
        "config_path": str(path),
        "kind": spec.kind,
    }
    if not path.is_file():
        return IntegrationResult(
            connected=False,
            changed=False,
            message=f"O {spec.name} já estava desconectado.",
            **result_kwargs,
        )
    text = path.read_text(encoding="utf-8")
    _validate_toml(text, path)
    updated, changed = _remove_toml_block(text)
    if not changed:
        return IntegrationResult(
            connected=False,
            changed=False,
            message=f"O {spec.name} já estava desconectado.",
            **result_kwargs,
        )
    _validate_toml(updated, path)
    _backup(path)
    _atomic_write(path, updated)
    return IntegrationResult(
        connected=False,
        changed=True,
        message=f"Desconectado do {spec.name}. {_RESTART_HINT}",
        **result_kwargs,
    )


def _merge_toml_entry(
    existing: Any,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    """Merge the BuscaTeses command into an existing server table."""
    entry: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    entry["command"] = command
    entry["args"] = list(args)
    _merge_entry_env(entry, env)
    return entry


def _merge_entry_env(entry: dict[str, Any], env: dict[str, str]) -> None:
    """Merge env vars, dropping a stale ``BUSCATESES_HOME`` and empty sections.

    When the app is back on the default data folder the caller passes an empty
    mapping; any ``BUSCATESES_HOME`` left from an earlier custom folder must go
    away instead of pointing the assistant at the wrong data.
    """
    current = entry.get("env")
    merged = dict(current) if isinstance(current, dict) else {}
    merged.update(env)
    if not env:
        merged.pop("BUSCATESES_HOME", None)
    if merged:
        entry["env"] = merged
    else:
        entry.pop("env", None)


def _toml_lines(entry: dict[str, Any]) -> list[str]:
    lines = [f"[{TOM_BLOCK}]"]
    for key, value in _ordered_items(entry):
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    return lines


def _ordered_items(entry: dict[str, Any]) -> list[tuple[str, Any]]:
    preferred = ("command", "args", "env")
    items = [(key, entry[key]) for key in preferred if key in entry]
    items.extend(
        (str(key), value)
        for key, value in entry.items()
        if key not in preferred
    )
    return items


def _toml_key(value: Any) -> str:
    key = str(value)
    return key if _TOML_KEY_RE.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, dict):
        return _toml_inline(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def _toml_inline(entry: dict[str, Any]) -> str:
    inner = ", ".join(
        f"{_toml_key(key)} = {_toml_value(value)}"
        for key, value in _ordered_items(entry)
    )
    return "{ " + inner + " }" if inner else "{}"


def _parse_toml(text: str, path: Path) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise IntegrationError(
            f"O arquivo {path} não é um TOML válido. Corrija-o antes de conectar."
        ) from exc
    return data if isinstance(data, dict) else {}


def _validate_toml(text: str, path: Path) -> None:
    _parse_toml(text, path)


def _lookup(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _header_path(line: str) -> tuple[str, ...] | None:
    """Return the key path of a single-line TOML table header."""
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    closing = stripped.find("]")
    if closing == -1:
        return None
    header = stripped[: closing + 1]
    try:
        data = tomllib.loads(header)
    except tomllib.TOMLDecodeError:
        return None
    return _single_key_path(data)


def _single_key_path(data: Any) -> tuple[str, ...] | None:
    if not isinstance(data, dict) or len(data) != 1:
        return None
    key = next(iter(data))
    path = [key]
    value = data[key]
    while isinstance(value, dict) and value:
        if len(value) != 1:
            return None
        key = next(iter(value))
        path.append(key)
        value = value[key]
    return tuple(path)


def _is_target_path(path: tuple[str, ...] | None) -> bool:
    return bool(path) and path[: len(_TOML_TARGET)] == _TOML_TARGET


def _upsert_toml_block(text: str, entry: dict[str, Any]) -> tuple[str, bool]:
    normalized, newline = _split_newlines(text)
    lines = normalized.split("\n")
    start = _find_block_start(lines)
    block = _toml_lines(entry)
    if start is not None:
        end = _find_block_end(lines, start)
        content_end = _content_end(lines, start, end)
        current = [line.rstrip() for line in lines[start:content_end]]
        if current == block:
            return text, False
        lines[start:content_end] = block
    else:
        assignment = _find_inline_assignment(lines)
        if assignment is None:
            if _lookup(_parse_toml(text, Path("config.toml")), _TOML_TARGET) is not None:
                raise IntegrationError(
                    "A configuração do Codex define o BuscaTeses em um formato que não "
                    "pode ser atualizado automaticamente. Edite o arquivo manualmente."
                )
            while lines and not lines[-1].strip():
                lines.pop()
            if lines:
                lines.append("")
            lines.extend(block)
        else:
            lines[assignment.index] = _inline_assignment_line(
                lines[assignment.index], entry, assignment
            )
    result = "\n".join(lines)
    if not result.endswith("\n"):
        result += "\n"
    return result.replace("\n", newline), True


def _remove_toml_block(text: str) -> tuple[str, bool]:
    normalized, newline = _split_newlines(text)
    lines = normalized.split("\n")
    start = _find_block_start(lines)
    if start is not None:
        end = _find_block_end(lines, start)
        del lines[start : _content_end(lines, start, end)]
    else:
        assignment = _find_inline_assignment(lines)
        if assignment is None:
            return text, False
        updated = _inline_without_entry(lines[assignment.index], assignment)
        if updated is None:
            del lines[assignment.index]
        else:
            lines[assignment.index] = updated
    while len(lines) > 1 and not lines[-1].strip() and not lines[-2].strip():
        lines.pop()
    result = "\n".join(lines)
    if result and not result.endswith("\n"):
        result += "\n"
    return result.replace("\n", newline), True


def _find_block_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _header_path(line) == _TOML_TARGET:
            return index
    return None


def _find_block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines):
        path = _header_path(lines[end])
        if path is not None and not _is_target_path(path):
            break
        end += 1
    return end


def _content_end(lines: list[str], start: int, end: int) -> int:
    """Keep trailing blank lines and comments that precede the next table."""
    content_end = end
    while content_end > start + 1:
        stripped = lines[content_end - 1].strip()
        if stripped and not stripped.startswith("#"):
            break
        content_end -= 1
    return content_end


@dataclass(frozen=True)
class _InlineAssignment:
    """A one-line table assignment that holds the BuscaTeses entry."""

    index: int
    key_path: tuple[str, ...]
    relative: tuple[str, ...]


def _find_inline_assignment(lines: list[str]) -> _InlineAssignment | None:
    """Locate a one-line inline table that defines the BuscaTeses server."""
    current_path: tuple[str, ...] = ()
    for index, line in enumerate(lines):
        path = _header_path(line)
        if path is not None:
            current_path = path
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key_path = _assignment_key_path(line)
        if key_path is None:
            continue
        full_path = current_path + key_path
        if full_path == _TOML_TARGET:
            relative: tuple[str, ...] = ()
        elif _TOML_TARGET[: len(full_path)] == full_path:
            relative = _TOML_TARGET[len(full_path) :]
        else:
            continue
        candidate = line
        if current_path:
            candidate = _render_header(current_path) + "\n" + line
        try:
            document = tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        value = _lookup(document, _TOML_TARGET)
        if isinstance(value, dict):
            return _InlineAssignment(index, key_path, relative)
    return None


def _assignment_key_path(line: str) -> tuple[str, ...] | None:
    """Return the dotted key written on the left side of an assignment."""
    key_text, _, _ = line.partition("=")
    try:
        document = tomllib.loads(f"{key_text.strip()} = 0")
    except tomllib.TOMLDecodeError:
        return None
    return _single_key_path(document)


def _inline_assignment_line(
    line: str,
    entry: dict[str, Any],
    assignment: _InlineAssignment,
) -> str:
    """Rewrite only the BuscaTeses value, keeping sibling inline entries."""
    prefix = line.split("=", 1)[0].rstrip()
    document = tomllib.loads(line)
    value = _lookup(document, assignment.key_path)
    if not isinstance(value, dict):
        return f"{prefix} = {_toml_inline(entry)}"
    updated = _copy_with_entry(value, assignment.relative, entry)
    return f"{prefix} = {_toml_value(updated)}"


def _copy_with_entry(
    value: dict[str, Any],
    relative: tuple[str, ...],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy of ``value`` with ``entry`` placed at ``relative``."""
    updated = dict(value)
    if not relative:
        return dict(entry)
    current = updated
    for key in relative[:-1]:
        child = current.get(key)
        child = dict(child) if isinstance(child, dict) else {}
        current[key] = child
        current = child
    current[relative[-1]] = dict(entry)
    return updated


def _inline_without_entry(
    line: str,
    assignment: _InlineAssignment,
) -> str | None:
    """Remove the entry from an inline assignment, preserving sibling servers."""
    prefix = line.split("=", 1)[0].rstrip()
    document = tomllib.loads(line)
    value = _lookup(document, assignment.key_path)
    if not isinstance(value, dict):
        return None
    if not assignment.relative:
        return None
    remaining = _copy_without_path(value, assignment.relative)
    if remaining is None:
        return None
    return f"{prefix} = {_toml_value(remaining)}"


def _copy_without_path(
    value: dict[str, Any], relative: tuple[str, ...]
) -> dict[str, Any] | None:
    """Return a copy of ``value`` without ``relative``, or None when empty."""
    updated = dict(value)
    current = updated
    for key in relative[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            return updated
        child = dict(child)
        current[key] = child
        current = child
    current.pop(relative[-1], None)
    return updated or None


def _render_header(path: tuple[str, ...]) -> str:
    return "[" + ".".join(_toml_key(key) for key in path) + "]"


def _toml_has_entry(text: str) -> bool:
    if not text.strip():
        return False
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    return isinstance(_lookup(document, _TOML_TARGET), dict)


def _split_newlines(text: str) -> tuple[str, str]:
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), newline


# -- Claude Code ----------------------------------------------------------------


def _claude_add_args(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> list[str]:
    """Build the arguments for ``claude mcp add`` (without the binary)."""
    arguments = ["mcp", "add", "--scope", "user"]
    for key, value in (env or {}).items():
        arguments.extend(["--env", f"{key}={value}"])
    arguments.extend(["buscateses", "--", command, *args])
    return arguments


def _connect_claude_code(
    spec: ClientSpec,
    home: Path,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> IntegrationResult:
    binary = shutil.which("claude")
    snippet = _snippet(spec, command, args, env)
    if binary is None:
        return IntegrationResult(
            id=spec.id,
            connected=False,
            changed=False,
            message=(
                "O Claude Code não foi encontrado no PATH. Instale-o ou copie o comando: "
                f"{snippet}"
            ),
            config_path=None,
            kind=spec.kind,
        )
    add_args = _claude_add_args(command, args, env)
    result = _run_claude(binary, add_args)
    if result.returncode == 0:
        return _connected_claude_result(spec)
    previous = _claude_code_entry(home)
    if not isinstance(previous, dict) or not previous.get("command"):
        raise _claude_failure(result)
    # An existing entry made ``add`` fail. Only now remove it, and restore it
    # if the replacement cannot be registered.
    removal = _run_claude(
        binary, ["mcp", "remove", "--scope", "user", "buscateses"]
    )
    if removal.returncode != 0:
        raise _claude_failure(removal)
    previous_env = previous.get("env")
    retry_env = (
        {str(key): str(value) for key, value in previous_env.items()}
        if isinstance(previous_env, dict)
        else {}
    )
    retry_env.update(env)
    if not env:
        retry_env.pop("BUSCATESES_HOME", None)
    retry = _run_claude(binary, _claude_add_args(command, args, retry_env))
    if retry.returncode == 0:
        return _connected_claude_result(spec)
    _restore_claude_entry(binary, previous)
    raise _claude_failure(retry)


def _connected_claude_result(spec: ClientSpec) -> IntegrationResult:
    return IntegrationResult(
        id=spec.id,
        connected=True,
        changed=True,
        message=(
            "Conectado ao Claude Code no escopo de usuário. "
            "Reinicie o Claude Code para carregar o servidor."
        ),
        config_path=None,
        kind=spec.kind,
    )


def _claude_failure(result: subprocess.CompletedProcess[str]) -> IntegrationError:
    detail = (result.stderr or result.stdout or "").strip()
    return IntegrationError(
        "O Claude Code não aceitou a configuração. "
        + (detail[:300] if detail else "Tente novamente.")
    )


def _claude_code_entry(home: Path) -> dict[str, Any] | None:
    """Return the current stdio entry registered for Claude Code, if any."""
    for candidate in (home / ".claude.json", home / ".claude/settings.json"):
        try:
            data = _load_json(candidate, allow_missing=True)
        except IntegrationError:
            continue
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and isinstance(servers.get("buscateses"), dict):
            entry = servers["buscateses"]
            return dict(entry) if isinstance(entry, dict) else None
    return None


def _restore_claude_entry(binary: str, entry: dict[str, Any]) -> bool:
    """Try to re-register a previously working Claude Code entry."""
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return False
    env = entry.get("env")
    environment = {
        str(key): str(value) for key, value in env.items()
    } if isinstance(env, dict) else {}
    args = [str(item) for item in entry.get("args") or []]
    try:
        result = _run_claude(binary, _claude_add_args(command, args, environment))
    except IntegrationError:
        return False
    return result.returncode == 0


def _disconnect_claude_code(spec: ClientSpec) -> IntegrationResult:
    binary = shutil.which("claude")
    if binary is None:
        return IntegrationResult(
            id=spec.id,
            connected=False,
            changed=False,
            message="O Claude Code não foi encontrado no PATH.",
            config_path=None,
            kind=spec.kind,
        )
    result = _run_claude(binary, ["mcp", "remove", "--scope", "user", "buscateses"])
    changed = result.returncode == 0
    return IntegrationResult(
        id=spec.id,
        connected=False,
        changed=changed,
        message=(
            "Desconectado do Claude Code. Reinicie o Claude Code."
            if changed
            else "O BuscaTeses não estava conectado ao Claude Code."
        ),
        config_path=None,
        kind=spec.kind,
    )


def _run_claude(
    binary: str,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [binary, *arguments],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrationError(
            "Não foi possível executar o Claude Code. Verifique a instalação e tente novamente."
        ) from exc


# -- small helpers --------------------------------------------------------------


def _backup(path: Path | None) -> None:
    """Keep the first backup, so the user's original file is never replaced."""
    if path is None or not path.is_file():
        return
    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if backup.exists():
        return
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        raise IntegrationError(
            "Não foi possível criar o backup da configuração do cliente."
        ) from exc


def _atomic_write(path: Path, payload: str) -> None:
    """Write atomically, following symlinks and preserving the file mode."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        mode = None
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "CLIENTS",
    "ClientSpec",
    "IntegrationError",
    "IntegrationResult",
    "connect",
    "disconnect",
    "list_integrations",
    "mcp_command",
    "mcp_env",
]
