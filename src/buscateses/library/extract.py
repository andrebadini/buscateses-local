"""Text extraction for the supported user document formats (SPEC §7)."""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from charset_normalizer import from_bytes

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".epub": "epub",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".html": "html",
    ".htm": "html",
}
OCR_MIN_CHARS_PER_PAGE = 30
MAX_EXTRACTED_CHARS = 50_000_000
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
#: ZIP members whose uncompressed size counts toward the extraction budget.
#: Images and fonts inside EPUB/DOCX files are never decompressed, so they must
#: not consume the text budget (a single cover image could otherwise reject a
#: perfectly readable book).
_ZIP_TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".csv",
        ".htm",
        ".html",
        ".json",
        ".md",
        ".markdown",
        ".ncx",
        ".opf",
        ".svg",
        ".text",
        ".tsv",
        ".txt",
        ".xht",
        ".xhtml",
        ".xml",
    }
)
_ZIP_LIMIT_MESSAGE = (
    "O arquivo compactado é grande demais para a biblioteca. "
    "Envie um documento menor."
)
_MAX_METADATA_CHARS = 400
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_WORD_FILE_RE = re.compile(r"^microsoft\s+word\s*[-–—]\s*(.+)$", re.IGNORECASE)
_UNMEANINGFUL_TITLES = frozenset(
    {
        "",
        "document",
        "documento",
        "documento sem titulo",
        "microsoft word",
        "sem titulo",
        "sem título",
        "documento sem título",
        "unknown",
        "untitled",
        "untitled document",
    }
)
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DC_NS = "http://purl.org/dc/elements/1.1/"


class ExtractionError(RuntimeError):
    """Raised when a document cannot be read or has an unsupported format."""


@dataclass(frozen=True)
class ExtractedPage:
    """One page (or spine item) of extracted text."""

    page_number: int
    text: str


@dataclass
class ExtractionResult:
    """Text and lightweight metadata extracted from a document."""

    pages: list[ExtractedPage] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    year: int | None = None
    kind: str = ""
    needs_ocr: bool = False

    @property
    def chars(self) -> int:
        """Return the total number of non-whitespace characters."""
        return sum(len(page.text.strip()) for page in self.pages)


def extract_document(path: str | Path, *, kind: str | None = None) -> ExtractionResult:
    """Extract text and metadata from ``path``.

    ``kind`` overrides the extension (used for uploaded temporary files).
    """
    source = Path(path)
    if not source.is_file():
        raise ExtractionError("O arquivo não foi encontrado.")
    resolved_kind = _normalize_kind(kind or source.suffix)
    if resolved_kind is None:
        raise ExtractionError(
            "Formato não suportado. Envie PDF, EPUB, DOCX, TXT, Markdown ou HTML."
        )
    try:
        if resolved_kind == "pdf":
            result = _extract_pdf(source)
        elif resolved_kind == "epub":
            result = _extract_epub(source)
        elif resolved_kind == "docx":
            result = _extract_docx(source)
        elif resolved_kind == "html":
            result = _extract_plain(source, strip_html=True)
        else:
            result = _extract_plain(source, strip_html=False)
    except ExtractionError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ExtractionError(
            "Não foi possível ler este documento. O arquivo pode estar corrompido."
        ) from exc

    result.kind = resolved_kind
    result.title = meaningful_title(result.title) or humanize_filename_title(source.stem)
    if result.chars > MAX_EXTRACTED_CHARS:
        raise ExtractionError(
            "O documento gera texto demais para a biblioteca. Envie um arquivo menor."
        )
    if resolved_kind == "pdf":
        average = result.chars / max(1, len(result.pages))
        result.needs_ocr = average < OCR_MIN_CHARS_PER_PAGE
    return result


def _normalize_kind(value: str) -> str | None:
    extension = "." + str(value).strip().lower().lstrip(".")
    return SUPPORTED_EXTENSIONS.get(extension)


def _clean_metadata(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:_MAX_METADATA_CHARS] or None


def meaningful_title(value: str | None) -> str | None:
    """Return a usable metadata title, or ``None`` for generic placeholders.

    Word processors often store ``Microsoft Word - name.doc`` as the title;
    in that case the document name (without its extension) is used.
    """
    text = _clean_metadata(value)
    if not text:
        return None
    word_match = _WORD_FILE_RE.match(text)
    if word_match:
        text = _clean_metadata(word_match.group(1))
        if not text:
            return None
        text = re.sub(r"\.(?:docx?|rtf)$", "", text, flags=re.IGNORECASE).strip()
    if text.casefold() in _UNMEANINGFUL_TITLES:
        return None
    return text or None


def humanize_filename_title(value: str | None) -> str:
    """Turn a file name into a readable title.

    Underscores and hyphens become spaces, the extension is dropped, repeated
    spaces collapse, and all-lower/all-upper names get sentence casing.
    """
    name = str(value or "").strip()
    if not name:
        return ""
    stem = Path(name).stem or name
    text = " ".join(stem.replace("_", " ").replace("-", " ").split())
    if not text:
        return ""
    if text.islower() or text.isupper():
        lowered = text.lower()
        text = lowered[:1].upper() + lowered[1:]
    return text[:_MAX_METADATA_CHARS]


def _year_from(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR_RE.search(str(value))
    return int(match.group(0)) if match else None


def _clean_text(value: str) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if not line and cleaned and not cleaned[-1]:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _decode_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            return data.decode("utf-8", errors="replace")
        return str(best)


def _extract_pdf(path: Path) -> ExtractionResult:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001 - library raises many exception types
        raise ExtractionError(
            "Não foi possível abrir o PDF. Verifique se o arquivo está íntegro."
        ) from exc
    pages: list[ExtractedPage] = []
    try:
        metadata = document.get_metadata_dict() or {}
        for index in range(len(document)):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()
                finally:
                    text_page.close()
            finally:
                page.close()
            pages.append(ExtractedPage(index + 1, _clean_text(text)))
    finally:
        document.close()
    return ExtractionResult(
        pages=pages,
        title=_clean_metadata(metadata.get("Title")),
        author=_clean_metadata(metadata.get("Author")),
        year=_year_from(metadata.get("CreationDate")),
    )


def _open_checked_zip(path: Path) -> zipfile.ZipFile:
    """Open a ZIP after enforcing limits on uncompressed text members.

    Only text, XML and HTML members are counted: EPUB/DOCX images and fonts are
    never read by the extractor, so they cannot exhaust memory or disk here.
    """
    archive = zipfile.ZipFile(path)
    try:
        total = 0
        for info in archive.infolist():
            if not _is_text_zip_member(info.filename):
                continue
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ExtractionError(_ZIP_LIMIT_MESSAGE)
            total += info.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ExtractionError(_ZIP_LIMIT_MESSAGE)
    except BaseException:
        archive.close()
        raise
    return archive


def _is_text_zip_member(name: str) -> bool:
    """Return whether a ZIP member is text-like and will be decompressed."""
    return posixpath.splitext(str(name))[1].lower() in _ZIP_TEXT_SUFFIXES


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    """Read one ZIP member with a hard byte limit (guards forged headers)."""
    info = archive.getinfo(name)
    if info.file_size > MAX_ZIP_MEMBER_BYTES:
        raise ExtractionError(_ZIP_LIMIT_MESSAGE)
    with archive.open(name) as handle:
        data = handle.read(MAX_ZIP_MEMBER_BYTES + 1)
    if len(data) > MAX_ZIP_MEMBER_BYTES:
        raise ExtractionError(_ZIP_LIMIT_MESSAGE)
    return data


def _extract_epub(path: Path) -> ExtractionResult:
    with _open_checked_zip(path) as archive:
        names = set(archive.namelist())
        container = _read_xml(archive, "META-INF/container.xml")
        opf_path = _epub_opf_path(container)
        if opf_path is None or opf_path not in names:
            raise ExtractionError("O EPUB não possui um arquivo de conteúdo válido.")
        package = _read_xml(archive, opf_path)
        base_dir = posixpath.dirname(opf_path)
        manifest: dict[str, tuple[str, str]] = {}
        for item in _iter_local(package, "item"):
            identifier = item.get("id")
            href = item.get("href")
            if identifier and href:
                manifest[identifier] = (
                    posixpath.normpath(posixpath.join(base_dir, href)).lstrip("/"),
                    item.get("media-type", ""),
                )
        spine_order = [
            ref.get("idref")
            for ref in _iter_local(package, "itemref")
            if ref.get("idref")
        ]
        documents = [
            manifest[identifier][0]
            for identifier in spine_order
            if identifier in manifest
        ]
        documents = [name for name in documents if name in names and not name.endswith(".ncx")]
        if not documents:
            documents = sorted(
                name
                for name in names
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            )
        pages: list[ExtractedPage] = []
        for index, name in enumerate(documents):
            raw = _read_member(archive, name)
            text = _html_to_text(_decode_bytes(raw))
            pages.append(ExtractedPage(index + 1, _clean_text(text)))
    title = _first_local_text(package, "title")
    author = _first_local_text(package, "creator")
    year = _year_from(_first_local_text(package, "date"))
    return ExtractionResult(pages=pages, title=title, author=author, year=year)


def _epub_opf_path(container: ElementTree.Element) -> str | None:
    for element in container.iter():
        if _local_name(element.tag) == "rootfile":
            full_path = element.get("full-path")
            if full_path:
                return posixpath.normpath(full_path).lstrip("/")
    return None


def _read_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    return ElementTree.fromstring(_read_member(archive, name))


def _iter_local(root: ElementTree.Element, name: str) -> Iterable[ElementTree.Element]:
    for element in root.iter():
        if _local_name(element.tag) == name:
            yield element


def _first_local_text(root: ElementTree.Element, name: str) -> str | None:
    for element in _iter_local(root, name):
        if element.text and element.text.strip():
            return _clean_metadata(element.text)
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from HTML, ignoring scripts and styles."""

    _BLOCK_TAGS = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "blockquote",
        "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.author: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
        elif lowered == "title":
            self._in_title = True
        elif lowered == "meta":
            values = {key.lower(): (value or "") for key, value in attrs}
            if values.get("name", "").lower() == "author" and values.get("content"):
                self.author = _clean_metadata(values["content"])
        elif lowered in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template"}:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif lowered == "title":
            self._in_title = False
        elif lowered in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        else:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _html_to_text(markup: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _extract_plain(path: Path, *, strip_html: bool) -> ExtractionResult:
    with path.open("rb") as handle:
        raw = handle.read(MAX_TEXT_BYTES + 1)
    if len(raw) > MAX_TEXT_BYTES:
        raise ExtractionError(
            "O arquivo de texto é maior que o limite de 64 MB para a biblioteca."
        )
    if not raw:
        return ExtractionResult(pages=[ExtractedPage(1, "")])
    decoded = _decode_bytes(raw)
    title: str | None = None
    author: str | None = None
    if strip_html:
        parser = _HTMLTextExtractor()
        parser.feed(decoded)
        parser.close()
        text = parser.text()
        title = _clean_metadata(parser.title)
        author = parser.author
    else:
        text = decoded
    parts = [part for part in text.split("\f")]
    pages = [
        ExtractedPage(index + 1, _clean_text(part))
        for index, part in enumerate(parts or [""])
    ]
    return ExtractionResult(pages=pages, title=title, author=author)


def _extract_docx(path: Path) -> ExtractionResult:
    with _open_checked_zip(path) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ExtractionError("O DOCX não possui um documento de texto válido.")
        root = _read_xml(archive, "word/document.xml")
        pages: list[list[str]] = [[]]
        _walk_docx(root, pages)
        title = author = None
        year = None
        if "docProps/core.xml" in names:
            core = _read_xml(archive, "docProps/core.xml")
            title = _first_local_text(core, "title")
            author = _first_local_text(core, "creator")
            year = _year_from(
                _first_local_text(core, "created") or _first_local_text(core, "modified")
            )
    extracted = [
        ExtractedPage(index + 1, _clean_text("".join(parts)))
        for index, parts in enumerate(pages)
        if "".join(parts).strip() or index == 0
    ]
    return ExtractionResult(pages=extracted, title=title, author=author, year=year)


def _walk_docx(element: ElementTree.Element, pages: list[list[str]]) -> None:
    tag = _local_name(element.tag)
    if tag == "t":
        pages[-1].append(element.text or "")
        return
    if tag == "tab":
        pages[-1].append("\t")
        return
    if tag == "br":
        if element.get(f"{{{_WORD_NS}}}type") == "page":
            pages.append([])
        else:
            pages[-1].append("\n")
        return
    # ``lastRenderedPageBreak`` is a rendering hint that often duplicates an
    # explicit ``w:br type="page"``; counting only the explicit break keeps
    # each page from being split twice.
    if tag == "lastRenderedPageBreak":
        return
    for child in element:
        _walk_docx(child, pages)
    if tag == "p":
        pages[-1].append("\n")


__all__ = [
    "MAX_EXTRACTED_CHARS",
    "MAX_TEXT_BYTES",
    "MAX_ZIP_MEMBER_BYTES",
    "MAX_ZIP_TOTAL_BYTES",
    "OCR_MIN_CHARS_PER_PAGE",
    "SUPPORTED_EXTENSIONS",
    "ExtractedPage",
    "ExtractionError",
    "ExtractionResult",
    "extract_document",
]
