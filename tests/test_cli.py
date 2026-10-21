from __future__ import annotations

import json

import pytest

from buscateses import __version__
from buscateses.catalog.installer import CatalogInstaller
from buscateses.cli import (
    COMMAND_BUILDERS,
    COMMAND_HANDLERS,
    COMMAND_HELP,
    build_parser,
    main,
    register_command,
)


def test_version_command(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"BuscaTeses {__version__}"
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"BuscaTeses {__version__}"


def test_help_flag_lists_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "serve" in output
    assert "setup" in output


def test_status_json(app_env, capsys) -> None:
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["app"]["version"] == __version__
    assert payload["catalog"]["installed"] is False
    assert payload["data_dir"] == str(app_env["paths"].home)
    assert "catalog_missing" in payload["warnings"]


def test_doctor_alias(app_env, capsys) -> None:
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "Catálogo: não instalado" in output
    assert "Pasta de dados:" in output


def test_search_after_install(app_env, capsys) -> None:
    CatalogInstaller(app_env["paths"], app_env["settings"]).install()
    assert main(["search", "inclusiva", "--mode", "text", "--json", "--limit", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode_used"] == "text"
    assert len(payload["results"]) == 3
    assert payload["results"][0]["id"].startswith("capes:")


def test_search_without_catalog(app_env, capsys) -> None:
    assert main(["search", "inclusiva"]) == 1
    assert "catálogo ainda não foi instalado" in capsys.readouterr().err.lower()


def test_search_rejects_invalid_limit(app_env, capsys) -> None:
    CatalogInstaller(app_env["paths"], app_env["settings"]).install()
    assert main(["search", "inclusiva", "--limit", "500"]) == 2
    assert "--limit" in capsys.readouterr().err


def test_register_command_extension(capsys) -> None:
    def builder(parser) -> None:
        parser.add_argument("--name", default="mundo")

    def handler(args) -> int:
        print(f"olá {args.name}")
        return 0

    register_command("saudar", "Saudação de teste", builder, handler)
    try:
        parser = build_parser()
        assert "saudar" in parser.format_help()
        assert main(["saudar", "--name", "teste"]) == 0
        assert capsys.readouterr().out.strip() == "olá teste"
    finally:
        COMMAND_BUILDERS.pop("saudar", None)
        COMMAND_HELP.pop("saudar", None)
        COMMAND_HANDLERS.pop("saudar", None)
