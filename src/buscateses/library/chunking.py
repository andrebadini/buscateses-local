"""Split extracted pages into overlapping chunks while tracking page spans."""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from buscateses.library.extract import ExtractedPage

CHUNK_TARGET = 1000
CHUNK_OVERLAP = 150
_PAGE_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class TextChunk:
    """A piece of document text with its source page range."""

    text: str
    page_start: int
    page_end: int


def chunk_pages(
    pages: Sequence[ExtractedPage],
    *,
    target: int = CHUNK_TARGET,
    overlap: int = CHUNK_OVERLAP,
) -> list[TextChunk]:
    """Split ``pages`` into chunks of about ``target`` characters.

    Chunks may span page boundaries; ``page_start``/``page_end`` record the
    first and last page that contributed text. Consecutive chunks share about
    ``overlap`` characters so phrases cut by a boundary remain searchable.
    """
    if target <= 0:
        raise ValueError("O tamanho do trecho deve ser positivo.")
    if overlap < 0 or overlap >= target:
        raise ValueError("A sobreposição deve ser menor que o tamanho do trecho.")

    text, page_starts = _join_pages(pages)
    if not text.strip():
        return []
    starts = sorted(page_starts)
    page_numbers = [page for _offset, page in starts]
    offsets = [offset for offset, _page in starts]

    chunks: list[TextChunk] = []
    position = 0
    length = len(text)
    while position < length:
        end = min(position + target, length)
        if end < length:
            end = _break_position(text, position, end)
            if length - end < min(overlap, target // 4):
                end = length
        piece = text[position:end]
        stripped = piece.strip()
        if stripped:
            first_page = _page_at(offsets, page_numbers, position)
            last_page = _page_at(
                offsets, page_numbers, max(position, end - 1)
            )
            chunks.append(TextChunk(stripped, first_page, last_page))
        if end >= length:
            break
        next_position = max(end - overlap, position + 1)
        while next_position < length and text[next_position].isspace():
            next_position += 1
        position = next_position if next_position > position else end
    return chunks


def _join_pages(pages: Sequence[ExtractedPage]) -> tuple[str, list[tuple[int, int]]]:
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for page in pages:
        if parts:
            parts.append(_PAGE_SEPARATOR)
            cursor += len(_PAGE_SEPARATOR)
        content = page.text.strip()
        offsets.append((cursor, page.page_number))
        parts.append(content)
        cursor += len(content)
    return "".join(parts), offsets


def _break_position(text: str, position: int, end: int) -> int:
    floor = position + max(1, int((end - position) * 0.55))
    for separator in ("\n\n", ". ", "! ", "? ", ";\n"):
        index = text.rfind(separator, floor, end)
        if index != -1:
            return index + len(separator)
    index = text.rfind(" ", floor, end)
    return index if index > position else end


def _page_at(offsets: Sequence[int], pages: Sequence[int], position: int) -> int:
    index = bisect.bisect_right(offsets, position) - 1
    index = max(0, min(index, len(pages) - 1))
    return pages[index]


__all__ = ["CHUNK_OVERLAP", "CHUNK_TARGET", "TextChunk", "chunk_pages"]
