"""Build native BuscaTeses installers for the current platform.

The script installs the build requirements, generates PyInstaller artifacts,
runs the platform packaging step and writes ``SHA256SUMS.txt`` under
``packaging/output/``. A smoke test exercises the console executable before
the installer is assembled.

Usage::

    python packaging/build.py [--skip-deps] [--skip-smoke] [--skip-icons]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
ICONS = PACKAGING / "icons"
OUTPUT = PACKAGING / "output"
DIST = OUTPUT / "pyinstaller"
WORK = ROOT / "build" / "pyinstaller"
SPEC = PACKAGING / "buscateses.spec"
SMOKE_PORT = 8765
SMOKE_TIMEOUT = 90.0


class BuildError(RuntimeError):
    """Raised when a build step fails."""


def app_version(root: Path = ROOT) -> str:
    """Read ``__version__`` from the package without importing it."""
    text = (root / "src" / "buscateses" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise BuildError("Não foi possível ler a versão do BuscaTeses.")
    return match.group(1)


def platform_key() -> tuple[str, str]:
    """Return ``(os_name, arch)`` normalized for artifact names."""
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
        return "macos", arch
    if sys.platform.startswith("win"):
        return "windows", "x64"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        arch = "x86_64" if machine in {"x86_64", "amd64"} else machine or "x86_64"
        return "linux", arch
    raise BuildError(f"Sistema operacional não suportado: {sys.platform}")


def artifact_name(os_name: str, arch: str) -> str:
    """Return the stable (versionless) installer file name."""
    if os_name == "macos":
        return f"BuscaTeses-macos-{arch}.dmg"
    if os_name == "windows":
        return "BuscaTeses-windows-x64-setup.exe"
    if os_name == "linux":
        return f"BuscaTeses-linux-{arch}.tar.gz"
    raise BuildError(f"Sistema operacional não suportado: {os_name}")


def sha256_file(path: Path) -> str:
    """Return the hexadecimal SHA-256 digest of ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(directory: Path, names: list[str] | None = None) -> Path:
    """Write ``SHA256SUMS.txt`` for the regular files in ``directory``."""
    target = directory / "SHA256SUMS.txt"
    if names is None:
        files = sorted(
            item
            for item in directory.iterdir()
            if item.is_file() and item.resolve() != target.resolve()
        )
    else:
        files = sorted(
            directory / name for name in names if (directory / name).resolve() != target.resolve()
        )
    lines = [f"{sha256_file(path)}  {path.name}" for path in files if path.is_file()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def install_build_requirements(python: str) -> None:
    """Install ``requirements-build.txt`` with uv when available, else pip."""
    requirements = PACKAGING / "requirements-build.txt"
    uv = shutil.which("uv")
    command = (
        [uv, "pip", "install", "-p", python, "-r", str(requirements)]
        if uv
        else [python, "-m", "pip", "install", "-r", str(requirements)]
    )
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise BuildError(
            "Falha ao instalar as dependências de build. "
            "Instale uv ou pip e tente novamente."
        )


def ensure_application_installed() -> None:
    """Fail early when the application dependencies are missing."""
    completed = subprocess.run(
        [sys.executable, "-c", "import buscateses"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise BuildError(
            "O pacote BuscaTeses não está instalado neste Python. "
            "Execute 'pip install -e .' antes do build."
        )


def required_icon(os_name: str) -> Path:
    """Return the icon file required by the platform packaging step."""
    if os_name == "macos":
        return ICONS / "icon.icns"
    if os_name == "windows":
        return ICONS / "icon.ico"
    return ICONS / "icon.png"


def ensure_icons(os_name: str) -> None:
    """Generate the icons only when the platform icon is missing."""
    if required_icon(os_name).is_file():
        return
    completed = subprocess.run(
        [sys.executable, str(ICONS / "make_icons.py")],
        check=False,
        cwd=ROOT,
    )
    if completed.returncode != 0 or not required_icon(os_name).is_file():
        raise BuildError("Não foi possível gerar os ícones do aplicativo.")


def run_pyinstaller() -> None:
    """Run PyInstaller with the project spec into ``packaging/output``."""
    DIST.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        str(SPEC),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(command, check=False, cwd=ROOT, env=environment)
    if completed.returncode != 0:
        raise BuildError("O PyInstaller falhou; veja a saída acima.")


def console_binary(os_name: str) -> Path:
    """Return the console executable produced by PyInstaller."""
    if os_name == "macos":
        return DIST / "BuscaTeses.app" / "Contents" / "MacOS" / "buscateses-cli"
    if os_name == "windows":
        return DIST / "BuscaTeses" / "buscateses-cli.exe"
    return DIST / "BuscaTeses" / "buscateses"


def _fetch_status(port: int, timeout: float = 1.0) -> dict[str, object] | None:
    """Return the ``/api/status`` payload when the server answers."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/status", timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_status(port: int, process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Poll the local server until it is ready or the process exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _fetch_status(port) is not None:
            return True
        time.sleep(0.25)
    return False


def _port_is_free(port: int) -> bool:
    """Return True when ``127.0.0.1:port`` can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_smoke_port(start: int = SMOKE_PORT, attempts: int = 20) -> int:
    """Return the first free port at or after ``start``."""
    for port in range(start, start + attempts):
        if _port_is_free(port):
            return port
    raise BuildError(f"Não há porta livre para o teste de fumaça a partir de {start}.")


def smoke_test(binary: Path, *, port: int | None = None) -> None:
    """Exercise ``--version``, ``status --json`` and ``serve`` on the binary."""
    if not binary.is_file():
        raise BuildError(f"Executável de console não encontrado: {binary}")
    if port is None:
        port = free_smoke_port()
    with tempfile.TemporaryDirectory(prefix="buscateses-smoke-") as home:
        environment = os.environ.copy()
        environment["BUSCATESES_HOME"] = home
        environment.setdefault("PYTHONIOENCODING", "utf-8")

        version = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
        if version.returncode != 0 or "BuscaTeses" not in version.stdout:
            raise BuildError(
                "O teste de fumaça de '--version' falhou: "
                f"{version.stdout.strip()} {version.stderr.strip()}"
            )

        status = subprocess.run(
            [str(binary), "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
        )
        if status.returncode != 0:
            raise BuildError(
                "'status --json' falhou: "
                f"{status.stdout.strip()} {status.stderr.strip()}"
            )
        try:
            payload = json.loads(status.stdout)
        except ValueError as exc:
            raise BuildError(f"'status --json' não retornou JSON: {exc}") from exc
        if not isinstance(payload, dict) or "app" not in payload:
            raise BuildError("'status --json' retornou um JSON inesperado.")

        log_path = Path(home) / "smoke-serve.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(binary), "serve", "--no-browser", "--port", str(port)],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                if not _wait_for_status(port, process, SMOKE_TIMEOUT):
                    log.flush()
                    output = log_path.read_text(encoding="utf-8", errors="replace")
                    raise BuildError(
                        "O servidor de teste não respondeu em "
                        f"http://127.0.0.1:{port}/api/status.\n{output[-3000:]}"
                    )
                response = _fetch_status(port)
                if response is None:
                    raise BuildError("A resposta de /api/status foi inválida.")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def find_iscc() -> Path:
    """Locate the Inno Setup 6 command-line compiler."""
    for variable in ("INNO_SETUP_COMPILER", "ISCC"):
        value = os.environ.get(variable)
        if value and Path(value).is_file():
            return Path(value)
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return Path(found)
    for base in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
    ):
        if not base:
            continue
        for version in ("Inno Setup 6", "Inno Setup 7"):
            candidate = Path(base) / version / "ISCC.exe"
            if candidate.is_file():
                return candidate
    raise BuildError(
        "Inno Setup não encontrado. Instale em https://jrsoftware.org/isinfo.php "
        "ou defina INNO_SETUP_COMPILER."
    )


def package_macos(output: Path) -> None:
    """Create the ad-hoc signed DMG through ``build_dmg.sh``."""
    app = DIST / "BuscaTeses.app"
    if not app.is_dir():
        raise BuildError("O bundle BuscaTeses.app não foi gerado pelo PyInstaller.")
    completed = subprocess.run(
        [str(PACKAGING / "macos" / "build_dmg.sh"), str(app), str(output)],
        check=False,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise BuildError("A criação do DMG falhou.")


def package_windows(output: Path) -> None:
    """Compile the Inno Setup installer."""
    source = DIST / "BuscaTeses"
    if not source.is_dir():
        raise BuildError("A pasta BuscaTeses não foi gerada pelo PyInstaller.")
    iscc = find_iscc()
    script = PACKAGING / "windows" / "buscateses.iss"
    command = [
        str(iscc),
        f"/DAppVersion={app_version()}",
        f"/DSourceDir={source}",
        f"/DOutputDir={output}",
        f"/DIconFile={ICONS / 'icon.ico'}",
        str(script),
    ]
    completed = subprocess.run(command, check=False, cwd=ROOT)
    if completed.returncode != 0:
        raise BuildError("A compilação do instalador Inno Setup falhou.")


def package_linux(output: Path) -> None:
    """Create the portable tarball with ``build_tar.sh``."""
    source = DIST / "BuscaTeses"
    if not source.is_dir():
        raise BuildError("A pasta BuscaTeses não foi gerada pelo PyInstaller.")
    completed = subprocess.run(
        [str(PACKAGING / "linux" / "build_tar.sh"), str(source), str(output)],
        check=False,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise BuildError("A criação do pacote .tar.gz falhou.")


def build(*, install_deps: bool = True, run_smoke: bool = True, icons: bool = True) -> Path:
    """Build the installer for the current platform and return its path."""
    os_name, arch = platform_key()
    ensure_application_installed()
    if install_deps:
        install_build_requirements(sys.executable)
    if icons:
        ensure_icons(os_name)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    run_pyinstaller()
    binary = console_binary(os_name)
    if run_smoke:
        smoke_test(binary)
    if os_name == "macos":
        package_macos(OUTPUT)
    elif os_name == "windows":
        package_windows(OUTPUT)
    else:
        package_linux(OUTPUT)
    artifact = OUTPUT / artifact_name(os_name, arch)
    if not artifact.is_file():
        raise BuildError(f"O instalador esperado não foi criado: {artifact}")
    write_checksums(OUTPUT)
    return artifact


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-deps", action="store_true", help="Não instalar dependências de build")
    parser.add_argument("--skip-smoke", action="store_true", help="Pular o teste de fumaça")
    parser.add_argument("--skip-icons", action="store_true", help="Não gerar ícones ausentes")
    arguments = parser.parse_args(argv)
    try:
        artifact = build(
            install_deps=not arguments.skip_deps,
            run_smoke=not arguments.skip_smoke,
            icons=not arguments.skip_icons,
        )
    except (BuildError, subprocess.SubprocessError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    size = artifact.stat().st_size
    print("\nBuild concluído.")
    print(f"Artefato: {artifact} ({size / (1024 * 1024):.1f} MB)")
    print(f"Somas SHA-256: {OUTPUT / 'SHA256SUMS.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
