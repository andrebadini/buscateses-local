"""Tests for document text extraction (SPEC §7)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fpdf import FPDF

from buscateses.library.extract import (
    ExtractionError,
    extract_document,
    humanize_filename_title,
    meaningful_title,
)


def _pdf(path: Path, *, text: str = "", pages: int = 1, title: str = "") -> Path:
    pdf = FPDF()
    pdf.set_author("Autora Teste")
    if title:
        pdf.set_title(title)
    for index in range(pages):
        pdf.add_page()
        if text:
            pdf.set_font("Helvetica", size=12)
            pdf.multi_cell(0, 8, f"{text} - pagina {index + 1}")
    pdf.output(str(path))
    return path


def _epub(path: Path) -> Path:
    container = """<?xml version="1.0"?>
<container version="1.0"
  xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    package = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Livro de Teste</dc:title>
    <dc:creator>Autora do EPUB</dc:creator>
    <dc:date>2019-04-02</dc:date>
  </metadata>
  <manifest>
    <item id="cap1" href="cap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="cap2" href="cap2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="cap1"/>
    <itemref idref="cap2"/>
  </spine>
</package>
"""
    first = """<html><head><title>Capítulo 1</title></head>
<body><h1>Primeiro capítulo</h1><p>Texto do primeiro capítulo.</p>
<script>alert('ignorar')</script></body></html>
"""
    second = """<html><body><p>Segundo capítulo com educação inclusiva.</p></body></html>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/cap1.xhtml", first)
        archive.writestr("OEBPS/cap2.xhtml", second)
    return path


def _docx(path: Path) -> Path:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Primeira página do DOCX.</w:t></w:r></w:p>
    <w:p><w:r><w:br w:type="page"/><w:t>Segunda página do DOCX.</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    core = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:title>Tese Sintética</dc:title>
  <dc:creator>Autor do DOCX</dc:creator>
  <dcterms:created>2021-08-10T10:00:00Z</dcterms:created>
</cp:coreProperties>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/core.xml", core)
    return path


def test_extract_pdf_text_and_metadata(tmp_path: Path) -> None:
    path = _pdf(
        tmp_path / "tese.pdf",
        text="Esta tese investiga a educação inclusiva no Brasil.",
        pages=2,
        title="Educação Inclusiva",
    )
    result = extract_document(path)
    assert result.kind == "pdf"
    assert len(result.pages) == 2
    assert "educação inclusiva" in result.pages[0].text.lower()
    assert result.title == "Educação Inclusiva"
    assert result.author == "Autora Teste"
    assert result.needs_ocr is False
    assert result.chars > 0


def test_extract_pdf_without_text_needs_ocr(tmp_path: Path) -> None:
    result = extract_document(_pdf(tmp_path / "vazio.pdf"))
    assert result.needs_ocr is True
    assert result.pages
    assert result.pages[0].text == ""


def test_extract_epub_spine_and_metadata(tmp_path: Path) -> None:
    result = extract_document(_epub(tmp_path / "livro.epub"))
    assert result.kind == "epub"
    assert len(result.pages) == 2
    assert "Primeiro capítulo" in result.pages[0].text
    assert "educação inclusiva" in result.pages[1].text
    assert "alert" not in result.pages[0].text
    assert result.title == "Livro de Teste"
    assert result.author == "Autora do EPUB"
    assert result.year == 2019


def test_extract_docx_pages_and_metadata(tmp_path: Path) -> None:
    result = extract_document(_docx(tmp_path / "tese.docx"))
    assert result.kind == "docx"
    assert len(result.pages) == 2
    assert "Primeira página" in result.pages[0].text
    assert "Segunda página" in result.pages[1].text
    assert result.title == "Tese Sintética"
    assert result.author == "Autor do DOCX"
    assert result.year == 2021


def test_extract_txt_with_cp1252(tmp_path: Path) -> None:
    path = tmp_path / "nota.txt"
    path.write_bytes("Pesquisa sobre educação e saúde.\nSegunda linha.".encode("cp1252"))
    result = extract_document(path)
    assert result.kind == "txt"
    assert "educação" in result.pages[0].text
    assert result.title == "Nota"


def test_extract_txt_form_feed_creates_pages(tmp_path: Path) -> None:
    path = tmp_path / "paginas.md"
    path.write_text("Página um.\fPágina dois.", encoding="utf-8")
    result = extract_document(path)
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[1].text == "Página dois."


def test_extract_html_strips_scripts_and_reads_meta(tmp_path: Path) -> None:
    path = tmp_path / "pagina.html"
    path.write_text(
        """<html><head><title>Meu Artigo</title>
        <meta name="author" content="Pessoa Autora">
        <style>body { color: red; }</style></head>
        <body><p>Conteúdo visível.</p><script>segredo()</script></body></html>""",
        encoding="utf-8",
    )
    result = extract_document(path)
    assert result.kind == "html"
    assert "Conteúdo visível" in result.pages[0].text
    assert "segredo" not in result.pages[0].text
    assert result.title == "Meu Artigo"
    assert result.author == "Pessoa Autora"


def test_extract_rejects_unsupported_and_missing(tmp_path: Path) -> None:
    unsupported = tmp_path / "arquivo.doc"
    unsupported.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="Formato não suportado"):
        extract_document(unsupported)
    with pytest.raises(ExtractionError, match="não foi encontrado"):
        extract_document(tmp_path / "ausente.pdf")


def test_extract_corrupted_zip_is_friendly(tmp_path: Path) -> None:
    broken = tmp_path / "quebrado.epub"
    broken.write_bytes(b"isto nao e um zip")
    with pytest.raises(ExtractionError, match="corrompido"):
        extract_document(broken)


def _plain_docx(path: Path, text: str) -> Path:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return path


def test_zip_member_size_limit_rejects_compressed_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from buscateses.library import extract

    path = _plain_docx(tmp_path / "bomba.docx", "x" * 200)
    monkeypatch.setattr(extract, "MAX_ZIP_MEMBER_BYTES", 64)
    with pytest.raises(ExtractionError, match="compactado é grande demais"):
        extract_document(path)


def test_zip_total_size_limit_rejects_many_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from buscateses.library import extract

    path = tmp_path / "muitos.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.xml", b"a" * 40)
        archive.writestr("b.xml", b"b" * 40)
        archive.writestr("word/document.xml", "<w:document/>")
    monkeypatch.setattr(extract, "MAX_ZIP_MEMBER_BYTES", 100)
    monkeypatch.setattr(extract, "MAX_ZIP_TOTAL_BYTES", 70)
    with pytest.raises(ExtractionError, match="compactado é grande demais"):
        extract_document(path)


def test_zip_total_limit_ignores_images_and_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from buscateses.library import extract

    path = tmp_path / "com-imagens.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/media/cover.png", b"p" * 200)
        archive.writestr("word/fonts/fonte.ttf", b"f" * 200)
        archive.writestr("word/media/figura.jpg", b"j" * 200)
    monkeypatch.setattr(extract, "MAX_ZIP_TOTAL_BYTES", 50)
    # Images and fonts are never decompressed, so they do not count.
    result = extract_document(path)
    assert result.kind == "docx"

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<document/>" + "x" * 80)
    with pytest.raises(ExtractionError, match="compactado é grande demais"):
        extract_document(path)


def test_text_file_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from buscateses.library import extract

    path = tmp_path / "grande.txt"
    path.write_bytes(b"a" * 64)
    monkeypatch.setattr(extract, "MAX_TEXT_BYTES", 32)
    with pytest.raises(ExtractionError, match="64 MB"):
        extract_document(path)


def test_docx_explicit_page_break_is_not_counted_twice(tmp_path: Path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Primeira página.</w:t></w:r></w:p>
    <w:p><w:r><w:br w:type="page"/><w:lastRenderedPageBreak/>
      <w:t>Segunda página.</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    path = tmp_path / "paginas.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    result = extract_document(path)
    assert len(result.pages) == 2
    assert "Segunda página" in result.pages[1].text


def test_humanize_filename_title() -> None:
    assert humanize_filename_title("meu_arquivo-final.pdf") == "Meu arquivo final"
    assert humanize_filename_title("MEU_ARQUIVO.pdf") == "Meu arquivo"
    assert humanize_filename_title("Meu_Arquivo.pdf") == "Meu Arquivo"
    assert humanize_filename_title("relatorio_final") == "Relatorio final"


def test_meaningful_title_ignores_placeholders() -> None:
    assert meaningful_title(None) is None
    assert meaningful_title("") is None
    assert meaningful_title("untitled") is None
    assert meaningful_title("Sem título") is None
    assert meaningful_title("Microsoft Word - x.doc") == "x"
    assert meaningful_title("Microsoft Word - Relatório Final.docx") == "Relatório Final"
    assert meaningful_title("Livro de Teste") == "Livro de Teste"


def test_extract_uses_humanized_filename_when_metadata_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "meu_arquivo-final.txt"
    path.write_text("Pesquisa sobre educação.", encoding="utf-8")
    result = extract_document(path)
    assert result.title == "Meu arquivo final"


def test_extract_cleans_word_processor_metadata_title(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "documento.pdf", text="Conteúdo", title="Microsoft Word - x.doc")
    result = extract_document(path)
    assert result.title == "x"
