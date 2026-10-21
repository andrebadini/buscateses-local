"""Generate PNG, ICO and ICNS icons from the project SVG.

The primary renderer is :mod:`cairosvg`. When the native cairo library is not
available (common on development machines and CI runners), the script falls
back to a headless Chromium binary such as the one downloaded by Playwright.

Usage::

    python packaging/icons/make_icons.py [--svg PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = ((16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256))
ICNS_ENTRIES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SVG = ROOT / "src" / "buscateses" / "web" / "assets" / "brand" / "icon.svg"


def render_with_cairosvg(svg: Path, out_dir: Path) -> bool:
    """Render every PNG size with cairosvg; return ``False`` when unavailable."""
    try:
        import cairosvg
    except (ImportError, OSError):
        return False
    for size in PNG_SIZES:
        cairosvg.svg2png(
            url=str(svg),
            write_to=str(out_dir / f"icon-{size}.png"),
            output_width=size,
            output_height=size,
        )
    return True


def _chromium_candidates() -> list[str]:
    """Return likely Chromium/Chrome executable paths for the current OS."""
    candidates: list[str] = []
    for variable in ("BUSCATESES_CHROMIUM", "CHROME_PATH", "PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
        value = os.environ.get(variable)
        if value:
            candidates.append(value)
    home = Path.home()
    patterns: list[str] = []
    if sys.platform == "darwin":
        patterns += [
            str(
                home
                / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
                / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            ),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("win"):
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        patterns += [
            str(local / "ms-playwright/chromium-*/chrome-win*/chrome.exe"),
            str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
        ]
    else:
        patterns += [
            str(home / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ]
    for pattern in patterns:
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return [item for item in candidates if Path(item).is_file()]


def _find_chromium() -> str | None:
    """Return the first usable Chromium executable, if any."""
    candidates = _chromium_candidates()
    return candidates[0] if candidates else None


def render_with_chromium(svg: Path, out_dir: Path) -> bool:
    """Render a 1024px PNG with headless Chromium and downscale it."""
    from PIL import Image

    chromium = _find_chromium()
    if chromium is None:
        return False
    with tempfile.TemporaryDirectory(prefix="buscateses-icon-") as temporary:
        shot = Path(temporary) / "icon.png"
        profile = Path(temporary) / "profile"
        command = [
            chromium,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--default-background-color=00000000",
            f"--window-size={PNG_SIZES[-1]},{PNG_SIZES[-1]}",
            f"--screenshot={shot}",
            svg.resolve().as_uri(),
        ]
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
        # Some Chromium builds exit with a non-zero status after writing the
        # screenshot ("teardown watchdog expired"); the file is what matters.
        del completed
        if not shot.is_file() or shot.stat().st_size == 0:
            return False
        with Image.open(shot) as rendered:
            master = rendered.convert("RGBA")
        if master.size != (PNG_SIZES[-1], PNG_SIZES[-1]):
            master = master.resize((PNG_SIZES[-1], PNG_SIZES[-1]), Image.Resampling.LANCZOS)
        for size in PNG_SIZES:
            if size == master.size[0]:
                image = master.copy()
            else:
                image = master.resize((size, size), Image.Resampling.LANCZOS)
            image.save(out_dir / f"icon-{size}.png")
    return True


def make_ico(out_dir: Path) -> None:
    """Build a multi-resolution Windows ``.ico`` file."""
    from PIL import Image

    with Image.open(out_dir / "icon-256.png") as source:
        source.convert("RGBA").save(out_dir / "icon.ico", format="ICO", sizes=ICO_SIZES)


def make_icns(out_dir: Path) -> bool:
    """Build a macOS ``.icns`` with ``iconutil``; return ``False`` if unavailable."""
    iconutil = shutil.which("iconutil")
    if iconutil is None or sys.platform != "darwin":
        return False
    with tempfile.TemporaryDirectory(prefix="buscateses-icns-") as temporary:
        iconset = Path(temporary) / "BuscaTeses.iconset"
        iconset.mkdir()
        for name, size in ICNS_ENTRIES.items():
            shutil.copyfile(out_dir / f"icon-{size}.png", iconset / name)
        completed = subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(out_dir / "icon.icns")],
            check=False,
            capture_output=True,
            text=True,
        )
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """Generate all icon files for the requested SVG."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--svg", type=Path, default=DEFAULT_SVG, help="Source SVG file")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent, help="Output directory")
    arguments = parser.parse_args(argv)
    svg = arguments.svg.resolve()
    out_dir = arguments.out.resolve()
    if not svg.is_file():
        print(f"Erro: SVG não encontrado: {svg}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_with_cairosvg(svg, out_dir)
    renderer = "cairosvg"
    if not rendered:
        rendered = render_with_chromium(svg, out_dir)
        renderer = "Chromium (Playwright)"
    if not rendered:
        print(
            "Erro: não foi possível renderizar o SVG. Instale cairosvg ou um "
            "Chromium/Chrome (variável BUSCATESES_CHROMIUM).",
            file=sys.stderr,
        )
        return 1
    shutil.copyfile(out_dir / "icon-512.png", out_dir / "icon.png")
    make_ico(out_dir)
    if not make_icns(out_dir) and sys.platform == "darwin":
        print("Aviso: iconutil não conseguiu gerar o .icns.", file=sys.stderr)
    print(f"Ícones gerados em {out_dir} (renderizador: {renderer}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
