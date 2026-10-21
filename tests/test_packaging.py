"""Offline tests for the packaging scripts (no builds, no network)."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
ICONS = PACKAGING / "icons"


def _load_build() -> ModuleType:
    """Load ``packaging/build.py`` without adding it to ``sys.path``."""
    path = PACKAGING / "build.py"
    spec = importlib.util.spec_from_file_location("buscateses_packaging_build", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("packaging/build.py not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build()


def test_app_version_matches_package() -> None:
    from buscateses import __version__

    assert build.app_version(ROOT) == __version__


def test_artifact_names_are_stable_and_versionless() -> None:
    assert build.artifact_name("macos", "arm64") == "BuscaTeses-macos-arm64.dmg"
    assert build.artifact_name("macos", "x86_64") == "BuscaTeses-macos-x86_64.dmg"
    assert build.artifact_name("windows", "x64") == "BuscaTeses-windows-x64-setup.exe"
    assert build.artifact_name("linux", "x86_64") == "BuscaTeses-linux-x86_64.tar.gz"


def test_platform_key_is_known() -> None:
    os_name, arch = build.platform_key()
    assert os_name in {"macos", "windows", "linux"}
    assert arch


def test_smoke_port_is_stable() -> None:
    assert build.SMOKE_PORT == 8765


def test_free_smoke_port_skips_busy_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]

        assert build._port_is_free(port) is False
        assert build.free_smoke_port(port) != port


def test_write_checksums(tmp_path: Path) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"conteudo-a")
    second.write_bytes(b"conteudo-b")
    (tmp_path / "SHA256SUMS.txt").write_text("lixo\n", encoding="utf-8")

    target = build.write_checksums(tmp_path)
    lines = target.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert lines[0] == f"{hashlib.sha256(b'conteudo-a').hexdigest()}  a.bin"
    assert lines[1] == f"{hashlib.sha256(b'conteudo-b').hexdigest()}  b.bin"


def test_entry_cli_reports_version() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(PACKAGING / "entry_cli.py"), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    assert completed.returncode == 0
    assert "BuscaTeses" in completed.stdout


def test_generated_icons_have_expected_signatures() -> None:
    for size in (16, 32, 48, 64, 128, 256, 512, 1024):
        assert (ICONS / f"icon-{size}.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (ICONS / "icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (ICONS / "icon.icns").read_bytes()[:4] == b"icns"


def test_pyinstaller_spec_has_two_executables_and_bundle() -> None:
    content = (PACKAGING / "buscateses.spec").read_text(encoding="utf-8")

    assert "entry_app.py" in content
    assert "entry_cli.py" in content
    assert 'name="BuscaTeses"' in content
    assert 'CLI_NAME = "buscateses" if sys.platform.startswith("linux") else "buscateses-cli"' in content
    assert "name=CLI_NAME" in content
    assert "console=False" in content
    assert "console=True" in content
    assert 'name="BuscaTeses.app"' in content
    assert "io.github.andrebadini.buscateses" in content
    assert "LSMinimumSystemVersion" in content
    assert "public.app-category.education" in content
    assert "upx=False" in content
    assert "tkinter" in content
    assert "uvicorn.loops.auto" in content


def test_pyinstaller_spec_skips_optional_subpackages() -> None:
    content = (PACKAGING / "buscateses.spec").read_text(encoding="utf-8")

    assert 'on_error="ignore"' in content
    assert "filter_submodules=" in content
    # mcp[cli] (typer) and dataset/onnx helpers must not be pulled into the app.
    assert '"mcp.cli"' in content
    assert '"onnxruntime.quantization"' in content
    assert '"onnxruntime.transformers"' in content
    assert '"webview.platforms.android"' in content
    assert '"webview.platforms.qt"' in content


def test_dataset_extra_declares_pipeline_dependencies() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["dataset"]
    names = {re.split(r"[<>=!~ ]", item, maxsplit=1)[0] for item in extra}

    assert {"duckdb", "tqdm", "pyarrow", "tokenizers", "numpy", "requests"} <= names


def test_inno_setup_is_per_user_with_portuguese() -> None:
    content = (PACKAGING / "windows" / "buscateses.iss").read_text(encoding="utf-8-sig")

    assert "AppId={{cbafab11-c1fd-4624-934f-c742f4aa5b81}" in content
    assert "PrivilegesRequired=lowest" in content
    assert r"{localappdata}\Programs\BuscaTeses" in content
    assert "BrazilianPortuguese.isl" in content
    assert "OutputBaseFilename=BuscaTeses-windows-x64-setup" in content
    assert "Deseja também apagar os dados" in content
    assert "Executar assim mesmo" in content


def test_macos_script_builds_stable_dmg() -> None:
    content = (PACKAGING / "macos" / "build_dmg.sh").read_text(encoding="utf-8")

    assert "codesign --force --deep --sign -" in content
    assert "BuscaTeses-macos-$ARCH.dmg" in content
    assert "buscateses-cli" in content
    assert "Como abrir pela primeira vez.txt" in content
    assert "-format UDZO" in content


def test_linux_tarball_includes_installer() -> None:
    content = (PACKAGING / "linux" / "build_tar.sh").read_text(encoding="utf-8")
    installer = (PACKAGING / "linux" / "install.sh").read_text(encoding="utf-8")

    assert "BuscaTeses-linux-$ARCH.tar.gz" in content
    assert "install.sh" in content
    assert ".local/opt/buscateses" in installer
    assert "buscateses.desktop" in installer


def test_source_installers_use_uv_and_setup() -> None:
    shell = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    for content in (shell, powershell):
        assert "astral.sh/uv" in content
        assert "uv tool install --python 3.12" in content
        assert "buscateses setup --yes" in content
        assert "connect" in content
    assert "--local" in shell
    assert "-Local" in powershell


def test_workflows_pin_action_versions() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    for content in (ci, release):
        assert "actions/checkout@v4" in content
        assert "actions/setup-python@v5" in content
    assert "actions/upload-artifact@v4" in ci
    assert "actions/upload-artifact@v4" in release
    assert "actions/download-artifact@v4" in release
    assert "softprops/action-gh-release@v2" in release
    assert "packaging/build.py" in release
    assert "pytest -q -m \"not slow\"" in ci
    assert 'pip install -e ".[dev,dataset]"' in ci
    assert "cache-dependency-path: pyproject.toml" in ci
    assert "requirements-build.txt" in release
    assert "choco install innosetup" in release
    assert "python packaging/build.py --skip-deps" in release


def test_ci_workflow_triggers() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" in ci
    assert "workflow_dispatch:" in ci


def test_tunnel_workflow_is_manual_and_minimal() -> None:
    path = ROOT / ".github" / "workflows" / "tunnel-e2e.yml"
    content = path.read_text(encoding="utf-8")

    import yaml  # provided by uvicorn[standard]

    data = yaml.safe_load(content)
    # PyYAML follows YAML 1.1 and reads the unquoted ``on`` key as ``True``.
    triggers = data.get("on", data.get(True))
    assert triggers == {"workflow_dispatch": None}
    assert "push:" not in content
    assert "pull_request:" not in content
    assert data["permissions"] == {"contents": "read"}
    assert "contents: write" not in content
    assert "secrets." not in content

    job = data["jobs"]["tunnel"]
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "macos-14",
        "windows-latest",
    ]
    steps = job["steps"]
    setup = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/setup-python")
    )
    assert setup["with"]["python-version"] == "3.12"
    install = next(step for step in steps if "pip install" in str(step.get("run", "")))
    assert 'pip install -e ".[dev]"' in install["run"]
    smoke = next(step for step in steps if "tunnel_smoke.py" in str(step.get("run", "")))
    assert smoke["run"] == "python tests/e2e/tunnel_smoke.py"
    assert smoke["timeout-minutes"] == 15
    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "tunnel-e2e-logs/"


def test_release_workflow_is_manual_and_validates_version() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    # Releases are always triggered by hand: no push/tag automation.
    assert "workflow_dispatch:" in release
    assert "push:" not in release
    assert "tags:" not in release
    assert "tag:" in release
    assert "required: true" in release
    assert "default: false" in release
    assert "prerelease: ${{ inputs.prerelease }}" in release
    assert "tag_name: ${{ inputs.tag }}" in release
    assert "target_commitish: ${{ github.sha }}" in release
    assert "ref: ${{ github.sha }}" in release
    # The job fails before building when the tag and __version__ disagree.
    assert "src/buscateses/__init__.py" in release
    assert "EXPECTED=\"${TAG#v}\"" in release
    assert "needs: validate" in release

