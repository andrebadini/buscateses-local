"""Offline checks for the public Markdown documentation."""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
# Screenshots are generated later from the real catalog; links to them are
# expected to be temporarily broken and must not fail this test.
IGNORED_LINK_DIR = (DOCS / "assets" / "screenshots").resolve()
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:")
_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _markdown_files() -> list[Path]:
    """Return the documentation files covered by the link check."""
    files = list(ROOT.glob("*.md"))
    files.extend(DOCS.rglob("*.md"))
    return sorted({path.resolve() for path in files if path.is_file()})


def _iter_links(text: str) -> Iterator[str]:
    """Yield link targets outside fenced code blocks."""
    inside_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue
        for match in _LINK_RE.finditer(line):
            yield match.group(1).strip()


def _relative_target(raw: str) -> str | None:
    """Normalize a Markdown target, or return ``None`` when it is external."""
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("#"):
        return None
    if target.lower().startswith(EXTERNAL_PREFIXES):
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    return path or None


def _is_ignored(path: Path) -> bool:
    """Return True for links inside the screenshots directory only."""
    try:
        path.resolve().relative_to(IGNORED_LINK_DIR)
    except ValueError:
        return False
    return True


def test_relative_markdown_links_exist() -> None:
    """Every relative link in the public docs points to an existing path."""
    broken: list[str] = []
    for markdown in _markdown_files():
        text = markdown.read_text(encoding="utf-8")
        for raw in _iter_links(text):
            relative = _relative_target(raw)
            if relative is None:
                continue
            target = (markdown.parent / relative).resolve()
            if _is_ignored(target):
                continue
            if not target.exists():
                broken.append(f"{markdown.relative_to(ROOT)}: {raw}")
    assert not broken, "Links relativos quebrados:\n" + "\n".join(broken)


def _load_build_module() -> ModuleType:
    """Load ``packaging/build.py`` without installing the package."""
    path = ROOT / "packaging" / "build.py"
    spec = importlib.util.spec_from_file_location("buscateses_docs_packaging", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("packaging/build.py não encontrado")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_readmes_mention_real_installer_names() -> None:
    """Both READMEs list the artifact names produced by ``packaging``."""
    build = _load_build_module()
    installers = {
        build.artifact_name("macos", "arm64"),
        build.artifact_name("macos", "x86_64"),
        build.artifact_name("windows", "x64"),
        build.artifact_name("linux", "x86_64"),
    }
    for name in ("README.md", "README.en.md"):
        content = (ROOT / name).read_text(encoding="utf-8")
        missing = sorted(item for item in installers if item not in content)
        assert not missing, f"{name} não menciona: {', '.join(missing)}"


def test_docs_have_no_unfilled_release_markers() -> None:
    """Release placeholders ({{RECORDS}} etc.) must be filled before publishing."""
    markers = ("{{RECORDS}}", "{{DISK}}", "{{DOWNLOAD}}", "{{DISK_INSTALLED}}", "{{BY_DECADE_TABLE}}")
    files = [ROOT / "README.md", ROOT / "README.en.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in files:
        content = path.read_text(encoding="utf-8")
        left = [marker for marker in markers if marker in content]
        assert not left, f"{path.name} ainda tem marcadores: {', '.join(left)}"
