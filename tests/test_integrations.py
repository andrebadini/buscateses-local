"""Tests for AI client MCP configuration (SPEC §10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from buscateses import integrations
from buscateses.integrations import (
    IntegrationError,
    connect,
    disconnect,
    list_integrations,
    mcp_command,
)


def test_mcp_command_uses_python_module() -> None:
    command, args = mcp_command()
    assert command == sys.executable
    assert args == ["-m", "buscateses", "mcp"]


@pytest.mark.parametrize(
    ("platform_name", "console_name"),
    [
        ("darwin", "buscateses-cli"),
        ("linux", "buscateses"),
        ("win32", "buscateses-cli.exe"),
    ],
)
def test_mcp_command_finds_packaged_console_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    console_name: str,
) -> None:
    executable = tmp_path / ("BuscaTeses.exe" if platform_name == "win32" else "BuscaTeses")
    executable.write_text("", encoding="utf-8")
    console = tmp_path / console_name
    console.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "platform", platform_name)

    command, args = mcp_command()

    assert command == str(console)
    assert args == ["mcp"]


def test_list_integrations_describes_all_clients(tmp_path: Path) -> None:
    rows = list_integrations(tmp_path)
    ids = {row["id"] for row in rows}
    assert {
        "claude_desktop",
        "cursor",
        "lm_studio",
        "gemini_cli",
        "codex",
        "claude_code",
        "vscode",
    } == ids
    assert "chatgpt" not in ids
    assert "claude_web" not in ids
    manual = next(row for row in rows if row["id"] == "vscode")
    assert manual["kind"] == "manual"
    assert "docs/" not in manual["snippet"]
    assert "docs/" not in manual["instructions"]
    assert manual["connected"] is False
    claude = next(row for row in rows if row["id"] == "claude_desktop")
    assert claude["install_url"] == "https://claude.ai/download"
    vscode = next(row for row in rows if row["id"] == "vscode")
    payload = json.loads(vscode["snippet"])
    assert payload["servers"]["buscateses"]["type"] == "stdio"


def test_json_connect_preserves_other_servers_and_backs_up(tmp_path: Path) -> None:
    config = tmp_path / ".cursor/mcp.json"
    config.parent.mkdir(parents=True)
    original = {"mcpServers": {"outro": {"command": "x"}}, "tema": "escuro"}
    config.write_text(json.dumps(original), encoding="utf-8")

    result = connect("cursor", home=tmp_path)
    assert result.connected is True
    assert result.changed is True
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["tema"] == "escuro"
    assert data["mcpServers"]["outro"] == {"command": "x"}
    assert data["mcpServers"]["buscateses"]["args"] == ["-m", "buscateses", "mcp"]
    backup = config.with_name(config.name + ".bak-buscateses")
    assert json.loads(backup.read_text(encoding="utf-8")) == original

    again = connect("cursor", home=tmp_path)
    assert again.changed is False

    removed = disconnect("cursor", home=tmp_path)
    assert removed.changed is True
    after = json.loads(config.read_text(encoding="utf-8"))
    assert "buscateses" not in after["mcpServers"]
    assert after["mcpServers"]["outro"] == {"command": "x"}
    assert disconnect("cursor", home=tmp_path).changed is False


def test_jsonc_is_reported_clearly_and_file_is_untouched(tmp_path: Path) -> None:
    config = tmp_path / ".cursor/mcp.json"
    config.parent.mkdir(parents=True)
    original = '{\n  // configuração do Cursor\n  "mcpServers": {}\n}\n'
    config.write_text(original, encoding="utf-8")

    with pytest.raises(IntegrationError, match="JSONC"):
        connect("cursor", home=tmp_path)
    assert config.read_text(encoding="utf-8") == original


def test_backup_keeps_the_original_file(tmp_path: Path) -> None:
    config = tmp_path / ".cursor/mcp.json"
    config.parent.mkdir(parents=True)
    original = json.dumps({"mcpServers": {"outro": {"command": "x"}}})
    config.write_text(original, encoding="utf-8")

    connect("cursor", home=tmp_path)
    backup = config.with_name(config.name + ".bak-buscateses")
    assert backup.read_text(encoding="utf-8") == original

    # A later disconnect must not replace the first backup.
    config.write_text(
        json.dumps({"mcpServers": {"buscateses": {"command": "y"}}}),
        encoding="utf-8",
    )
    disconnect("cursor", home=tmp_path)
    assert backup.read_text(encoding="utf-8") == original


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_symlinked_config_writes_to_target_and_keeps_mode(tmp_path: Path) -> None:
    real = tmp_path / "config-real.json"
    real.write_text('{"mcpServers": {}}', encoding="utf-8")
    real.chmod(0o640)
    config = tmp_path / ".cursor/mcp.json"
    config.parent.mkdir(parents=True)
    config.symlink_to(real)

    result = connect("cursor", home=tmp_path)
    assert result.changed is True
    assert config.is_symlink()
    payload = json.loads(real.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["buscateses"]["command"] == sys.executable
    assert real.stat().st_mode & 0o777 == 0o640


def test_mcp_env_only_for_non_default_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "dados-padrao"
    monkeypatch.setattr(integrations, "user_data_path", lambda *args, **kwargs: default)
    assert integrations.mcp_env(default) == {}
    assert integrations.mcp_env(tmp_path / "outro") == {
        "BUSCATESES_HOME": str((tmp_path / "outro").resolve())
    }


def test_connect_adds_buscateses_home_for_custom_data_folder(tmp_path: Path) -> None:
    data_home = tmp_path / "dados"
    result = connect("cursor", home=tmp_path, data_home=data_home)
    payload = json.loads(Path(result.config_path or "").read_text(encoding="utf-8"))
    entry = payload["mcpServers"]["buscateses"]
    assert entry["env"] == {"BUSCATESES_HOME": str(data_home.resolve())}

    snippet = json.loads(
        next(row for row in list_integrations(tmp_path, data_home=data_home)
             if row["id"] == "cursor")["snippet"]
    )
    assert snippet["mcpServers"]["buscateses"]["env"]["BUSCATESES_HOME"] == str(
        data_home.resolve()
    )


def test_connect_removes_custom_data_home_when_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "dados-padrao"
    monkeypatch.setattr(integrations, "user_data_path", lambda *args, **kwargs: default)
    connect("cursor", home=tmp_path, data_home=tmp_path / "dados")
    connect("cursor", home=tmp_path, data_home=default)

    config = tmp_path / ".cursor/mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    entry = payload["mcpServers"]["buscateses"]
    assert "env" not in entry

    # Unrelated variables set by the user survive; only BUSCATESES_HOME goes.
    payload["mcpServers"]["buscateses"]["env"] = {
        "BUSCATESES_HOME": "/dados",
        "OUTRA": "1",
    }
    config.write_text(json.dumps(payload), encoding="utf-8")
    connect("cursor", home=tmp_path, data_home=default)
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["buscateses"]
    assert entry["env"] == {"OUTRA": "1"}


def test_codex_connect_removes_custom_data_home_when_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "dados-padrao"
    monkeypatch.setattr(integrations, "user_data_path", lambda *args, **kwargs: default)
    connect("codex", home=tmp_path, data_home=tmp_path / "dados")
    connect("codex", home=tmp_path, data_home=default)

    config = tmp_path / ".codex/config.toml"
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert "env" not in data["mcp_servers"]["buscateses"]


def test_json_connect_creates_file_and_directory(tmp_path: Path) -> None:
    result = connect("lm_studio", home=tmp_path)
    assert result.changed is True
    payload = json.loads(Path(result.config_path or "").read_text(encoding="utf-8"))
    assert payload["mcpServers"]["buscateses"]["command"] == sys.executable


def test_invalid_json_is_left_untouched(tmp_path: Path) -> None:
    config = tmp_path / ".cursor/mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{ isto nao e json", encoding="utf-8")
    with pytest.raises(IntegrationError, match="JSON válido"):
        connect("cursor", home=tmp_path)
    assert config.read_text(encoding="utf-8") == "{ isto nao e json"
    with pytest.raises(IntegrationError, match="JSON válido"):
        disconnect("cursor", home=tmp_path)
    assert not config.with_name(config.name + ".bak-buscateses").exists()


def test_codex_toml_connect_and_disconnect(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "gpt-5"\n\n[shell]\napproval = "never"\n', encoding="utf-8")

    result = connect("codex", home=tmp_path)
    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["model"] == "gpt-5"
    assert data["shell"]["approval"] == "never"
    assert data["mcp_servers"]["buscateses"]["args"] == [
        "-m",
        "buscateses",
        "mcp",
    ]
    assert connect("codex", home=tmp_path).changed is False

    disconnect("codex", home=tmp_path)
    after = tomllib.loads(config.read_text(encoding="utf-8"))
    assert "buscateses" not in after.get("mcp_servers", {})
    assert after["model"] == "gpt-5"
    assert after["shell"]["approval"] == "never"
    backup = config.with_name(config.name + ".bak-buscateses")
    # The first backup is kept and still holds the file as it was before us.
    assert backup.read_text(encoding="utf-8") == (
        'model = "gpt-5"\n\n[shell]\napproval = "never"\n'
    )


def test_codex_toml_quoted_key_and_following_comments_are_preserved(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "# topo\n"
        "[modelo]\n"
        'x = 1\n'
        "\n"
        '[mcp_servers."buscateses"]\n'
        'command = "antigo"\n'
        "args = []\n"
        "\n"
        "# comentário do próximo bloco\n"
        "[shell]\n"
        'approval = "never"\n',
        encoding="utf-8",
    )

    result = connect("codex", home=tmp_path)
    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["mcp_servers"]["buscateses"]["command"] == sys.executable
    assert data["mcp_servers"]["buscateses"]["args"] == ["-m", "buscateses", "mcp"]
    assert "# topo" in text
    assert "# comentário do próximo bloco" in text
    assert data["shell"]["approval"] == "never"

    disconnect("codex", home=tmp_path)
    text = config.read_text(encoding="utf-8")
    assert "# comentário do próximo bloco" in text
    assert "[shell]" in text
    assert "buscateses" not in tomllib.loads(text).get("mcp_servers", {})


def test_codex_commented_entry_is_not_treated_as_connected(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '# [mcp_servers.buscateses]\n# command = "antigo"\n[shell]\napproval = "never"\n',
        encoding="utf-8",
    )

    result = connect("codex", home=tmp_path)
    assert result.changed is True
    text = config.read_text(encoding="utf-8")
    assert '# [mcp_servers.buscateses]' in text
    data = tomllib.loads(text)
    assert data["mcp_servers"]["buscateses"]["command"] == sys.executable
    assert data["shell"]["approval"] == "never"


def test_codex_inline_table_is_updated_and_keeps_siblings(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'model = "gpt-5"\n'
        'mcp_servers = { buscateses = { command = "antigo", args = [] }, '
        'outro = { command = "x" } }\n',
        encoding="utf-8",
    )

    result = connect("codex", home=tmp_path)
    assert result.changed is True
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    servers = data["mcp_servers"]
    assert servers["buscateses"]["command"] == sys.executable
    assert servers["buscateses"]["args"] == ["-m", "buscateses", "mcp"]
    assert servers["outro"] == {"command": "x"}
    assert data["model"] == "gpt-5"

    disconnect("codex", home=tmp_path)
    after = tomllib.loads(config.read_text(encoding="utf-8"))
    assert "buscateses" not in after["mcp_servers"]
    assert after["mcp_servers"]["outro"] == {"command": "x"}


def test_codex_inline_entry_inside_table_is_updated(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[mcp_servers]\n"
        'buscateses = { command = "antigo", args = [] }\n'
        'outro = { command = "x" }\n',
        encoding="utf-8",
    )

    connect("codex", home=tmp_path)
    after = tomllib.loads(config.read_text(encoding="utf-8"))
    assert after["mcp_servers"]["buscateses"]["command"] == sys.executable
    assert after["mcp_servers"]["outro"] == {"command": "x"}


def test_codex_unrewritable_form_aborts_without_changes(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    original = (
        "[mcp_servers]\n"
        'buscateses.command = "antigo"\n'
        "buscateses.args = []\n"
        "[shell]\n"
        'approval = "never"\n'
    )
    config.write_text(original, encoding="utf-8")

    with pytest.raises(IntegrationError, match="TOML válido|manualmente"):
        connect("codex", home=tmp_path)
    assert config.read_text(encoding="utf-8") == original


def test_codex_invalid_toml_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("isto = [", encoding="utf-8")
    with pytest.raises(IntegrationError, match="TOML válido"):
        connect("codex", home=tmp_path)
    assert config.read_text(encoding="utf-8") == "isto = ["


def test_manual_clients_do_not_change_files(tmp_path: Path) -> None:
    result = connect("vscode", home=tmp_path)
    assert result.changed is False
    assert result.connected is False
    assert "manual" in result.message.lower()
    assert disconnect("vscode", home=tmp_path).changed is False


def test_unknown_client_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(IntegrationError, match="desconhecido"):
        connect("inexistente", home=tmp_path)
    with pytest.raises(IntegrationError, match="desconhecido"):
        disconnect("inexistente", home=tmp_path)


def test_claude_code_uses_subprocess(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(integrations.subprocess, "run", fake_run)

    result = connect("claude_code", home=tmp_path)
    assert result.connected is True
    # Adding first keeps the previous entry intact if the command fails.
    assert commands[0][1:6] == ["mcp", "add", "--scope", "user", "buscateses"]
    assert commands[0][6] == "--"
    assert commands[0][7] == sys.executable

    assert disconnect("claude_code", home=tmp_path).changed is True
    assert commands[-1][1:5] == ["mcp", "remove", "--scope", "user"]


def test_claude_code_removes_existing_entry_only_after_failed_add(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "buscateses": {
                        "command": "/usr/bin/python",
                        "args": ["-m", "buscateses", "mcp"],
                        "env": {"BUSCATESES_HOME": "/dados", "OUTRA": "1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    add_results = iter([1, 0])

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["mcp", "add"]:
            return subprocess.CompletedProcess(
                command, next(add_results, 1), "", "já existe"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(integrations.subprocess, "run", fake_run)

    result = connect("claude_code", home=tmp_path)
    assert result.connected is True
    actions = [command[1:3] for command in commands]
    assert actions == [["mcp", "add"], ["mcp", "remove"], ["mcp", "add"]]
    # Back on the default data folder: only the stale BUSCATESES_HOME goes
    # away; unrelated variables are preserved.
    assert "BUSCATESES_HOME=/dados" not in commands[-1]
    assert "OUTRA=1" in commands[-1]


def test_claude_code_restores_entry_when_replacement_fails(
    monkeypatch, tmp_path: Path
) -> None:
    previous = {
        "command": "/usr/bin/python",
        "args": ["-m", "buscateses", "mcp"],
        "env": {"BUSCATESES_HOME": "/dados"},
    }
    (tmp_path / ".claude.json").write_text(
        json.dumps({"mcpServers": {"buscateses": previous}}),
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    add_results = iter([1, 1, 0])

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["mcp", "add"]:
            return subprocess.CompletedProcess(command, next(add_results, 1), "", "falhou")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(integrations.subprocess, "run", fake_run)

    with pytest.raises(IntegrationError, match="não aceitou"):
        connect("claude_code", home=tmp_path)

    adds = [command for command in commands if command[1:3] == ["mcp", "add"]]
    assert len(adds) == 3
    restore = adds[-1]
    assert "/usr/bin/python" in restore
    assert "BUSCATESES_HOME=/dados" in restore


def test_claude_code_reports_failures(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "falhou")

    monkeypatch.setattr(integrations.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(integrations.subprocess, "run", fake_run)
    with pytest.raises(IntegrationError, match="não aceitou"):
        connect("claude_code", home=tmp_path)


def test_claude_code_missing_binary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(integrations.shutil, "which", lambda name: None)
    result = connect("claude_code", home=tmp_path)
    assert result.connected is False
    assert "PATH" in result.message


def test_windows_appdata_path(monkeypatch, tmp_path: Path) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        pytest.skip("teste específico de simulação POSIX")
    monkeypatch.setattr(integrations.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    result = connect("claude_desktop", home=tmp_path)
    assert result.config_path is not None
    assert str(tmp_path / "Roaming") in result.config_path
    assert "Claude" in result.config_path
