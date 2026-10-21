"""Tests for progressive FTS relaxation and the text time/count budgets."""

from __future__ import annotations

import pytest

from buscateses.catalog.search import (
    CatalogSearch,
    SearchError,
    SearchQuery,
    TextQueryTimeout,
    parse_fts_query,
)
from buscateses.fts_relax import (
    document_frequencies,
    fold_term,
    relaxation_drops,
)


def test_relaxation_drops_most_frequent_terms_first() -> None:
    assert relaxation_drops([5, 90, 3]) == [1]
    assert relaxation_drops([5, 90, 3, 80]) == [1, 3]
    # Two terms are the floor: never relax below two expressions.
    assert relaxation_drops([5, 90]) == []


def test_fold_term_matches_fts_diacritics_removal() -> None:
    assert fold_term("Educação") == "educacao"
    assert fold_term("JUÍZES") == "juizes"


def test_document_frequencies_use_the_vocabulary(installed) -> None:
    catalog = installed["catalog"]
    parsed = parse_fts_query("educação saúde", "all")
    frequencies = document_frequencies(catalog._connection(), parsed.positives)
    # Both themes occur in eight synthetic records.
    assert frequencies == [8, 8]


def test_relaxation_drops_the_broad_term_before_or(installed) -> None:
    catalog = installed["catalog"]
    # "saúde" appears only in another theme; dropping it keeps the intersection
    # of "inclusiva" and "educação" instead of matching both themes with OR.
    result = catalog.search(SearchQuery(q="inclusiva educação saúde", mode="text"))
    assert result.relaxed is True
    assert result.total == 8
    assert all("saúde" not in (item["title"] or "").casefold() for item in result.results)


def test_text_count_is_capped(installed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("buscateses.catalog.search.TEXT_COUNT_LIMIT", 5)
    catalog = installed["catalog"]
    result = catalog.search(SearchQuery(q="participativos", mode="text"))
    assert result.total == 5
    assert result.total_capped is True
    assert result.total_is_estimate is True
    assert "total_capped" in result.to_dict()


def test_text_timeout_returns_friendly_error(installed) -> None:
    catalog = CatalogSearch(
        installed["release"],
        installed["dataset"].embedder,
        text_timeout=0.0,
        progress_steps=1,
    )
    with pytest.raises(TextQueryTimeout, match="demorou demais"):
        catalog.search(SearchQuery(q="inclusiva", mode="text"))
    assert issubclass(TextQueryTimeout, SearchError)


def test_hybrid_keeps_semantic_channel_when_text_times_out(installed) -> None:
    catalog = CatalogSearch(
        installed["release"],
        installed["dataset"].embedder,
        text_timeout=0.0,
        progress_steps=1,
    )
    result = catalog.search(SearchQuery(q="inclusiva", mode="hybrid"))
    assert "text_slow" in result.warnings
    assert result.mode_used == "hybrid"
    assert result.results
    assert all(item["matched_by"] == ["significado"] for item in result.results)


def test_catalog_warm_up_is_safe(installed) -> None:
    catalog = installed["catalog"]
    catalog.warm_up()
    assert catalog.stats()["records"] == 60
