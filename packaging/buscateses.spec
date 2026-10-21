# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for BuscaTeses (onedir, two executables).

The GUI executable ("BuscaTeses") and the console client ("buscateses") share a
single Analysis so the heavy dependencies are collected only once. The console
client is required by AI assistants configured through ``buscateses mcp``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    is_module_or_submodule,
)

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
ICONS = Path(SPECPATH).resolve() / "icons"
ENTRY_APP = str(Path(SPECPATH).resolve() / "entry_app.py")
ENTRY_CLI = str(Path(SPECPATH).resolve() / "entry_cli.py")


def app_version() -> str:
    """Read ``__version__`` without importing the application package."""
    text = (SRC / "buscateses" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


VERSION = app_version()

datas = [
    (str(SRC / "buscateses" / "web"), "buscateses/web"),
    (str(SRC / "buscateses" / "data"), "buscateses/data"),
]
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []


def _is_excluded(name: str, prefixes: tuple[str, ...]) -> bool:
    """Return True when ``name`` is one of the excluded (sub)packages."""
    return any(is_module_or_submodule(name, prefix) for prefix in prefixes)


def _collect_package(package: str, excluded: tuple[str, ...] = ()) -> None:
    """Collect a package, skipping optional subpackages that need extra deps.

    Some dependencies (``mcp``, ``onnxruntime``, ``webview``) ship optional
    subpackages whose import requires packages that are not installed for the
    release build.  PyInstaller may import the package tree while collecting
    submodules, so a missing optional dependency must never abort the build:
    we filter those subpackages and fall back to the base package when the
    collector cannot inspect the tree at all.
    """
    try:
        package_datas, package_binaries, package_hidden = collect_all(
            package,
            filter_submodules=lambda name: not _is_excluded(name, excluded),
            on_error="ignore",
        )
    except Exception:  # pragma: no cover - depends on the PyInstaller version
        datas.extend(collect_data_files(package))
        binaries.extend(collect_dynamic_libs(package))
        hiddenimports.append(package)
        return
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)


# Packages with dynamic imports or native libraries that PyInstaller cannot
# discover by static analysis alone. Optional extras that pull in packages we
# do not ship are excluded on purpose.
_onnxruntime_excluded = (
    "onnxruntime.backend",  # needs the optional "onnx" package
    "onnxruntime.quantization",  # needs "onnx"
    "onnxruntime.tools",  # needs "onnx"
    "onnxruntime.transformers",  # needs "onnx"/"psutil"
)
_collect_package("onnxruntime", _onnxruntime_excluded)
_collect_package("tokenizers")
_collect_package("pypdfium2")
_collect_package("pypdfium2_raw")

# Only the GUI backend available on the build platform is meaningful.
_webview_excluded = [
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.qt",
]
if sys.platform == "darwin":
    _webview_backend = "webview.platforms.cocoa"
    _webview_excluded += [
        "webview.platforms.gtk",
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "webview.platforms.win32",
        "webview.platforms.winforms",
    ]
elif sys.platform.startswith("win"):
    # EdgeChromium is the default; mshtml/winforms remain as fallbacks.
    _webview_backend = "webview.platforms.edgechromium"
    _webview_excluded += [
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
    ]
else:
    _webview_backend = "webview.platforms.gtk"
    _webview_excluded += [
        "webview.platforms.cocoa",
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "webview.platforms.win32",
        "webview.platforms.winforms",
    ]
_collect_package("webview", tuple(_webview_excluded))

# ``mcp.cli`` imports ``typer`` (the optional ``mcp[cli]`` extra), which the
# application does not use: only the server side is bundled.
_collect_package("mcp", ("mcp.cli",))

for package in (
    "uvicorn",
    "fastapi",
    "multipart",
    "charset_normalizer",
    "platformdirs",
):
    hiddenimports += collect_submodules(package, on_error="ignore")

hiddenimports += [
    # uvicorn resolves loop/protocol implementations dynamically.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvloop",
    "httptools",
    "websockets",
    "watchfiles",
    "h11",
    "anyio",
    "anyio._backends._asyncio",
    "sse_starlette",
    "sse_starlette.sse",
    "pydantic",
    "pydantic_core",
]

hiddenimports.append(_webview_backend)

# Keep the installer lean: none of these are imported by the application, but
# some optional onnxruntime/tokenizers helpers drag them in.
excludes = [
    "tkinter",
    "matplotlib",
    "torch",
    "torchvision",
    "torchaudio",
    "sentence_transformers",
    "transformers",
    "scipy",
    "sklearn",
    "sympy",
    "pandas",
    "IPython",
    "jupyter",
    "notebook",
    "tensorflow",
    "duckdb",
    "pytest",
    # Optional extras that are not used by the app and may not be installed.
    "mcp.cli",
    "typer",
    "onnx",
]
# Skip optional native backends and dataset-only helpers that static analysis
# may still discover inside the collected packages.
excludes += list(_onnxruntime_excluded)
excludes += _webview_excluded

analysis = Analysis(
    [ENTRY_APP, ENTRY_CLI],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)


def _scripts_for(entry: str) -> list[tuple[str, str, str]]:
    """Return runtime hooks plus the single entry script for one executable."""
    target = os.path.abspath(entry)
    runtime_hooks = [
        item for item in analysis.scripts if os.path.basename(item[1]).startswith("pyi_rth_")
    ]
    own = [item for item in analysis.scripts if os.path.abspath(item[1]) == target]
    if not own:
        raise SystemExit(f"Entry script not found in Analysis: {entry}")
    return runtime_hooks + own


if sys.platform == "darwin":
    app_icon = str(ICONS / "icon.icns")
elif sys.platform.startswith("win"):
    app_icon = str(ICONS / "icon.ico")
else:
    app_icon = None

gui = EXE(
    pyz,
    _scripts_for(ENTRY_APP),
    [],
    exclude_binaries=True,
    name="BuscaTeses",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

# macOS and Windows use case-insensitive file systems, where "BuscaTeses" and
# "buscateses" are the same path. Use a distinct name for the console client
# there so it does not overwrite the GUI executable.
CLI_NAME = "buscateses" if sys.platform.startswith("linux") else "buscateses-cli"

cli = EXE(
    pyz,
    _scripts_for(ENTRY_CLI),
    [],
    exclude_binaries=True,
    name=CLI_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

collection = COLLECT(
    gui,
    analysis.binaries,
    analysis.datas,
    cli,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BuscaTeses",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="BuscaTeses.app",
        icon=str(ICONS / "icon.icns"),
        bundle_identifier="io.github.andrebadini.buscateses",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "BuscaTeses",
            "CFBundleName": "BuscaTeses",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleIdentifier": "io.github.andrebadini.buscateses",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.education",
            "NSHumanReadableCopyright": "BuscaTeses contributors — MIT",
        },
    )
