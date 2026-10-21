"""Tests for page-aware overlapping chunking (SPEC §7)."""

from __future__ import annotations

import pytest

from buscateses.library.chunking import CHUNK_OVERLAP, CHUNK_TARGET, chunk_pages
from buscateses.library.extract import ExtractedPage


def test_chunks_cover_text_and_track_pages() -> None:
    pages = [
        ExtractedPage(1, "A" * 900),
        ExtractedPage(2, "B" * 900),
        ExtractedPage(3, "C" * 900),
    ]
    chunks = chunk_pages(pages)
    assert len(chunks) >= 3
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 3
    assert all(chunk.text for chunk in chunks)
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)
    joined = "".join(chunk.text for chunk in chunks)
    assert "A" in joined and "B" in joined and "C" in joined
    assert any(chunk.page_start == 1 and chunk.page_end == 2 for chunk in chunks)


def test_chunk_size_is_near_target_and_overlap_shared() -> None:
    text = " ".join(f"palavra{index:04d}" for index in range(400))
    chunks = chunk_pages([ExtractedPage(1, text)], target=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)
    assert all(len(chunk.text) >= 100 for chunk in chunks)
    overlaps = 0
    for previous, current in zip(chunks, chunks[1:], strict=False):
        tail = previous.text[-40:]
        if any(tail[index:] and tail[index:] in current.text for index in range(0, 30)):
            overlaps += 1
    assert overlaps >= len(chunks) - 2


def test_chunk_pages_respects_paragraph_break() -> None:
    text = ("primeiro parágrafo " * 30) + "\n\n" + ("segundo parágrafo " * 30)
    chunks = chunk_pages([ExtractedPage(1, text)], target=300, overlap=30)
    assert len(chunks) >= 2
    assert "segundo" not in chunks[0].text
    assert any("segundo parágrafo" in chunk.text for chunk in chunks)


def test_empty_pages_and_invalid_parameters() -> None:
    assert chunk_pages([]) == []
    assert chunk_pages([ExtractedPage(1, "   ")]) == []
    with pytest.raises(ValueError):
        chunk_pages([ExtractedPage(1, "x")], target=0)
    with pytest.raises(ValueError):
        chunk_pages([ExtractedPage(1, "x")], target=100, overlap=100)


def test_single_long_page_produces_single_chunk() -> None:
    chunks = chunk_pages([ExtractedPage(1, "texto curto")])
    assert len(chunks) == 1
    assert chunks[0].text == "texto curto"
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 1)
    assert CHUNK_TARGET == 1000
    assert CHUNK_OVERLAP == 150
