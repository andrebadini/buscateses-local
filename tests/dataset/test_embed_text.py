"""Tests for the document-text builder (no network, no real tokenizer)."""

from __future__ import annotations

from embed import build_document_text
from tokenizers import Tokenizer, models, pre_tokenizers, processors


def _build_tokenizer(words: list[str]) -> Tokenizer:
    vocabulary = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}
    for word in words:
        if word not in vocabulary:
            vocabulary[word] = len(vocabulary)
    tokenizer = Tokenizer(models.WordLevel(vocab=vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        special_tokens=[("[CLS]", vocabulary["[CLS]"]), ("[SEP]", vocabulary["[SEP]"])],
    )
    return tokenizer


def test_document_text_uses_title_and_abstract() -> None:
    tokenizer = _build_tokenizer(["title:", "|", "text:", "Meu", "título", "Resumo", "palavra"])
    text = build_document_text("Meu título", "Resumo palavra", "ignorada", tokenizer)
    assert text == "title: Meu título | text: Resumo palavra"


def test_document_text_falls_back_to_keywords_and_none_title() -> None:
    tokenizer = _build_tokenizer(["title:", "|", "text:", "none", "alfa", "beta"])
    assert build_document_text(None, None, "alfa; beta", tokenizer) == (
        "title: none | text: alfa; beta"
    )
    assert build_document_text("  ", "", None, tokenizer) == "title: none | text: "


def test_document_text_truncates_by_tokenizer_offsets() -> None:
    words = [f"w{index}" for index in range(40)]
    tokenizer = _build_tokenizer(["title:", "|", "text:"] + words)
    text = build_document_text("w0", " ".join(words[1:]), None, tokenizer, max_tokens=10)

    original = f"title: w0 | text: {' '.join(words[1:])}"
    assert text != original
    assert text.startswith("title: w0 | text: ")
    assert len(tokenizer.encode(text, add_special_tokens=True).ids) <= 10
    assert not text.endswith(" ")
    # The cut happens on a token boundary, not inside a word.
    remaining = original[len(text) :]
    assert remaining == "" or remaining.startswith(" ")


def test_document_text_returns_empty_when_only_special_tokens_fit() -> None:
    tokenizer = _build_tokenizer(["title:", "|", "text:", "alfa"])
    assert build_document_text("a", "alfa", None, tokenizer, max_tokens=2) == ""


def test_document_text_truecases_uppercase_metadata() -> None:
    tokenizer = _build_tokenizer(["title:", "|", "text:"])
    text = build_document_text(
        "COMPRESSAO PARALELA AS FIBRAS EM PECAS DE MADEIRA",
        "DISSERTACAO SOBRE EDUCACAO E SAUDE",
        None,
        tokenizer,
    )
    assert text == (
        "title: Compressão paralela as fibras em peças de madeira | "
        "text: Dissertação sobre educação e saúde"
    )


def test_document_text_truecases_keywords_fallback() -> None:
    tokenizer = _build_tokenizer(["title:", "|", "text:"])
    text = build_document_text(None, None, "EDUCACAO INCLUSIVA; SAUDE COLETIVA", tokenizer)
    assert text == "title: none | text: Educação inclusiva; saúde coletiva"
