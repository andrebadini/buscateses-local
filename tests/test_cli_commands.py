"""Tests for the new CLI subcommands (SPEC §11)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from buscateses import desktop, integrations
from buscateses.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]

TEXT = (
    "Documento da biblioteca sobre educação inclusiva e políticas públicas. "
) * 12


def test_parser_accepts_library_and_mcp_flags() -> None:
    parser = build_parser()
    library = parser.parse_args(["library", "add", "a.pdf", "b.epub"])
    assert library.library_command == "add"
    assert library.arquivos == ["a.pdf", "b.epub"]
    mcp = parser.parse_args(
        ["mcp", "--http", "--port", "9000", "--token", "abc", "--allow-no-token"]
    )
    assert mcp.http is True
    assert mcp.port == 9000
    assert mcp.token == "abc"
    assert mcp.allow_no_token is True
    assert parser.parse_args(["connect", "--list"]).list_clients is True
    assert parser.parse_args(["disconnect", "cursor"]).cliente == "cursor"
    remote_start = parser.parse_args(["remote", "start", "--include-library"])
    assert remote_start.acao == "start"
    assert remote_start.include_library is True
    assert parser.parse_args(["remote", "stop"]).acao == "stop"
    assert parser.parse_args(["remote", "status"]).acao == "status"


def test_remote_status_and_stop_when_off(app_env, capsys) -> None:
    assert main(["remote", "status"]) == 0
    assert "desligado" in capsys.readouterr().out.lower()
    assert main(["remote", "stop"]) == 0
    assert "desligado" in capsys.readouterr().out.lower()


def test_remote_status_reads_runtime_file(app_env, capsys) -> None:
    from buscateses.remote import write_runtime_file

    write_runtime_file(
        app_env["paths"],
        {
            "state": "online",
            "url": "https://exemplo.trycloudflare.com/mcp/segredo",
            "port": 8102,
            "include_library": True,
        },
    )
    try:
        assert main(["remote", "status"]) == 0
        output = capsys.readouterr().out
        assert "https://exemplo.trycloudflare.com/mcp/segredo" in output
        assert "biblioteca" in output.lower()
    finally:
        app_env["paths"].remote_runtime_file.unlink(missing_ok=True)


def test_library_add_list_and_remove(app_env, tmp_path: Path, capsys) -> None:
    source = tmp_path / "pesquisa.txt"
    source.write_text(TEXT, encoding="utf-8")
    assert main(["library", "add", str(source)]) == 0
    assert "adicionada à biblioteca" in capsys.readouterr().out

    assert main(["library", "list", "--json"]) == 0
    documents = json.loads(capsys.readouterr().out)
    assert len(documents) == 1
    doc_id = documents[0]["id"]
    assert documents[0]["status"] == "ready"

    assert main(["library", "list"]) == 0
    assert doc_id in capsys.readouterr().out

    assert main(["library", "remove", doc_id, "--yes"]) == 0
    assert "removido" in capsys.readouterr().out
    assert main(["library", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_library_remove_missing(app_env, capsys) -> None:
    assert main(["library", "remove", "nao-existe", "--yes"]) == 1
    assert "não encontrado" in capsys.readouterr().err


def test_library_empty_list_and_missing_command(app_env, capsys) -> None:
    assert main(["library", "list"]) == 0
    assert "vazia" in capsys.readouterr().out
    assert main(["library"]) == 2
    assert "library add" in capsys.readouterr().err


def test_library_add_failure_returns_error(app_env, tmp_path: Path, capsys) -> None:
    missing = tmp_path / "ausente.txt"
    assert main(["library", "add", str(missing)]) == 1
    assert "não foi encontrado" in capsys.readouterr().err


def test_library_reindex_without_model(app_env, capsys) -> None:
    assert main(["library", "reindex", "--yes"]) == 1
    assert "modelo" in capsys.readouterr().err.lower()


def test_without_arguments_opens_app(monkeypatch, capsys) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(desktop, "run_app", lambda: 0)
    assert main([]) == 0
    assert capsys.readouterr().out == ""


def test_help_lists_commands_without_opening_app(monkeypatch, capsys) -> None:
    def fail() -> int:
        raise AssertionError("o app não deve abrir com --help")

    monkeypatch.setattr(desktop, "run_app", fail)
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "app" in capsys.readouterr().out


def test_connect_list(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        integrations,
        "list_integrations",
        lambda **_kwargs: [
            {
                "id": "cursor",
                "name": "Cursor",
                "detected": True,
                "connected": True,
            }
        ],
    )
    assert main(["connect", "--list"]) == 0
    output = capsys.readouterr().out
    assert "cursor" in output and "Cursor" in output


def test_connect_manual_and_unknown(monkeypatch, capsys) -> None:
    assert main(["connect", "vscode"]) == 0
    assert "manual" in capsys.readouterr().out.lower()
    assert main(["disconnect", "vscode"]) == 0
    assert "manual" in capsys.readouterr().out.lower()
    assert main(["connect", "nao-existe"]) == 1
    assert "desconhecido" in capsys.readouterr().err.lower()
    assert main(["connect"]) == 2
    assert "cliente" in capsys.readouterr().err.lower()


def test_mcp_command_forwards_options(app_env, monkeypatch) -> None:
    from buscateses import mcp_server

    calls: list[tuple] = []
    monkeypatch.setattr(
        mcp_server,
        "run_stdio",
        lambda paths, settings: calls.append(("stdio", paths.home)),
    )
    monkeypatch.setattr(
        mcp_server,
        "run_http",
        lambda paths, settings, **kwargs: calls.append(("http", kwargs)),
    )
    assert main(["mcp"]) == 0
    assert calls[0] == ("stdio", app_env["paths"].home)
    assert main(
        [
            "mcp",
            "--http",
            "--port",
            "9000",
            "--token",
            "segredo",
            "--allow-no-token",
        ]
    ) == 0
    assert calls[1] == (
        "http",
        {
            "port": 9000,
            "token": "segredo",
            "allow_no_token": True,
            "allowed_hosts": None,
        },
    )
    assert main(
        [
            "mcp",
            "--http",
            "--allowed-host",
            "minha-maquina.tailnet.ts.net",
            "--allowed-host",
            "tunel.example",
        ]
    ) == 0
    assert calls[2] == (
        "http",
        {
            "port": None,
            "token": None,
            "allow_no_token": False,
            "allowed_hosts": [
                "minha-maquina.tailnet.ts.net",
                "tunel.example",
            ],
        },
    )


def test_serve_reports_busy_port(app_env, capsys) -> None:
    del app_env
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = int(sock.getsockname()[1])
        assert main(["serve", "--port", str(port), "--no-browser"]) == 2
    captured = capsys.readouterr()
    assert f"A porta {port} já está em uso" in captured.err
    assert f"--port {port + 10}" in captured.err
    assert "http://127.0.0.1" not in captured.out


def test_connect_reports_tool_result(monkeypatch, capsys) -> None:
    class Result:
        message = "Conectado ao Cursor."

    monkeypatch.setattr(integrations, "connect", lambda client, **_kwargs: Result())
    assert main(["connect", "cursor"]) == 0
    assert "Conectado ao Cursor" in capsys.readouterr().out


def _subprocess_env(home: Path, dataset_url: str | None = None) -> dict[str, str]:
    """Environment for a real CLI subprocess using the local source tree."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["BUSCATESES_HOME"] = str(home)
    if dataset_url is not None:
        env["BUSCATESES_DATASET_URL"] = dataset_url
    return env


def test_setup_writes_output_to_redirected_file(
    tmp_path: Path, dataset_factory
) -> None:
    dataset = dataset_factory()
    log_path = tmp_path / "setup.log"
    env = _subprocess_env(tmp_path / "home", str(dataset.root))

    with log_path.open("wb") as handle:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "buscateses",
                "setup",
                "--yes",
                "--only",
                "catalog",
            ],
            cwd=ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )

    assert result.returncode == 0
    output = log_path.read_text(encoding="utf-8")
    # A progress stage and the final summary reached the redirected file.
    assert "Ativando o novo catálogo" in output or "Baixando o catálogo" in output
    assert "Concluído." in output
    assert "Registros:" in output


def test_progress_lines_are_visible_before_process_exit(tmp_path: Path) -> None:
    """Redirected stdout must be line-buffered so progress is not stuck."""
    script = (
        "import time\n"
        "from buscateses.cli import _ProgressPrinter, _ensure_streams\n"
        "_ensure_streams()\n"
        'print("inicio do download")\n'
        "printer = _ProgressPrinter()\n"
        "printer.update({'stage': 'download', 'message': 'Baixando o catálogo…',\n"
        "                'progress': 0.0, 'bytes_done': 0, 'bytes_total': 1000})\n"
        "time.sleep(5)\n"
    )
    log_path = tmp_path / "progress.log"
    env = _subprocess_env(tmp_path / "home")
    with log_path.open("wb") as handle:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            output = ""
            while time.monotonic() < deadline and process.poll() is None:
                output = log_path.read_text(encoding="utf-8")
                if "Baixando o catálogo" in output and "0%" in output:
                    break
                time.sleep(0.05)
            assert "inicio do download" in output
            assert "Baixando o catálogo" in output
            assert "0%" in output
        finally:
            process.kill()
            process.wait(timeout=10)
