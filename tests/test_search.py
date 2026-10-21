from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest

from buscateses.catalog.builder import CATALOG_DB, VECTORS_FILE
from buscateses.catalog.search import (
    CatalogSearch,
    SearchError,
    SearchNotFoundError,
    SearchQuery,
    build_snippet,
    parse_fts_query,
)


@pytest.fixture
def catalog(installed) -> CatalogSearch:
    return installed["catalog"]


def test_text_search(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="inclusiva", mode="text"))
    assert result.mode_used == "text"
    assert result.total > 0
    assert result.total_is_estimate is False
    assert all(item["matched_by"] == ["texto"] for item in result.results)
    assert any("inclusiva" in (item["title"] or "").casefold() for item in result.results)
    assert result.facets["degree"]
    assert any("[[" in item["snippet"] for item in result.results)


def test_missing_record_raises_a_specific_exception(catalog: CatalogSearch) -> None:
    with pytest.raises(SearchNotFoundError, match="não encontrada"):
        catalog.get("capes:9999")
    with pytest.raises(SearchNotFoundError, match="não encontrada"):
        catalog.similar("capes:9999", 3)
    assert issubclass(SearchNotFoundError, SearchError)


def test_python_snippet_marks_accent_insensitive_matches() -> None:
    text = "Trecho inicial. " * 25 + "A EDUCAÇÃO INCLUSIVA é um direito. " + "Fim. " * 25
    snippet = build_snippet(text, ["educacao"])
    assert "[[EDUCAÇÃO]]" in snippet
    assert snippet.startswith("…")
    assert len(snippet) < len(text)


def test_python_snippet_supports_quoted_phrase() -> None:
    text = "Antes. " * 20 + "Políticas de educação inclusiva avançam. " + "Depois. " * 20
    snippet = build_snippet(text, ["educação inclusiva"])
    assert "[[educação inclusiva]]" in snippet


def test_python_snippet_without_terms_starts_at_beginning() -> None:
    text = "Primeiro parágrafo do resumo. " * 20
    snippet = build_snippet(text, [])
    assert snippet.startswith("Primeiro")
    assert snippet.endswith("…")


def test_semantic_search_marks_terms_in_snippet(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="inclusiva", mode="semantic", page_size=5))
    assert result.results
    assert any("[[" in item["snippet"] for item in result.results)


def test_semantic_search(catalog: CatalogSearch) -> None:
    result = catalog.search(
        SearchQuery(q="educação inclusiva", mode="semantic", page_size=10)
    )
    assert result.mode_used == "semantic"
    assert result.total_is_estimate is True
    assert result.results
    assert all(item["matched_by"] == ["significado"] for item in result.results)
    assert any("educação inclusiva" in (item["title"] or "") for item in result.results[:5])


def test_hybrid_search_combines_channels(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="inclusiva", mode="hybrid", page_size=20))
    assert result.mode_used == "hybrid"
    assert result.warnings == []
    matched = {tuple(item["matched_by"]) for item in result.results}
    assert ("texto", "significado") in matched
    assert ("significado",) in matched


def test_filters_are_applied(catalog: CatalogSearch) -> None:
    query = SearchQuery(
        q="inclusiva",
        mode="hybrid",
        degree=("Doutorado",),
        uf=("MG",),
        institution=("UFMG",),
        great_area=("CIENCIAS EXATAS",),
        year_min=2000,
        year_max=2020,
        page_size=50,
    )
    result = catalog.search(query)
    assert result.results
    for item in result.results:
        assert item["degree"] == "Doutorado"
        assert item["uf"] == "MG"
        assert item["institution_acronym"] == "UFMG"
        assert item["great_area"] == "Ciências Exatas"
        assert 2000 <= item["year"] <= 2020


def test_scope_title_and_abstract(catalog: CatalogSearch) -> None:
    title = catalog.search(SearchQuery(q="participativos", mode="text", scope="title"))
    abstract = catalog.search(
        SearchQuery(q="participativos", mode="text", scope="abstract", page_size=100)
    )
    assert title.total == 0
    assert abstract.total == 60
    author = catalog.search(SearchQuery(q="Sobrenome1", mode="text", scope="author"))
    assert author.total >= 1
    advisor = catalog.search(SearchQuery(q="Orientador", mode="text", scope="advisor"))
    assert advisor.total > 0


def test_relaxed_or_fallback(catalog: CatalogSearch) -> None:
    result = catalog.search(
        SearchQuery(q="inclusiva termoquesemduvidaexiste", mode="text")
    )
    assert result.relaxed is True
    assert result.total > 0


def test_sorting_and_pagination(catalog: CatalogSearch) -> None:
    descending = catalog.search(
        SearchQuery(q="inclusiva", mode="text", sort="year_desc", page_size=100)
    )
    ascending = catalog.search(
        SearchQuery(q="inclusiva", mode="text", sort="year_asc", page_size=100)
    )
    assert [item["year"] for item in descending.results] == sorted(
        [item["year"] for item in descending.results], reverse=True
    )
    assert [item["year"] for item in ascending.results] == sorted(
        [item["year"] for item in ascending.results]
    )
    first = catalog.search(SearchQuery(q="inclusiva", mode="text", page=1, page_size=5))
    second = catalog.search(SearchQuery(q="inclusiva", mode="text", page=2, page_size=5))
    assert len(first.results) == 5
    assert {item["id"] for item in first.results}.isdisjoint(
        {item["id"] for item in second.results}
    )
    assert first.pages == (first.total + 4) // 5


def test_hybrid_year_sort(catalog: CatalogSearch) -> None:
    result = catalog.search(
        SearchQuery(q="inclusiva", mode="hybrid", sort="year_desc", page_size=100)
    )
    years = [item["year"] for item in result.results]
    assert years == sorted(years, reverse=True)


@pytest.mark.parametrize(
    "query",
    [
        '"educação',
        "educação AND inclusiva",
        "educação OR inclusiva",
        "educação NEAR inclusiva",
        "educação*",
        "(educação inclusiva)",
        "educação -inclusiva",
        "educacao",
        'educação"); DROP TABLE theses; --',
        "saúde; pública",
    ],
)
def test_malicious_or_odd_queries_are_safe(catalog: CatalogSearch, query: str) -> None:
    try:
        result = catalog.search(SearchQuery(q=query, mode="text"))
    except SearchError:
        return
    assert isinstance(result.total, int)
    assert catalog.stats()["records"] == 60


def test_hyphen_exclusion(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="investiga -participativos", mode="text"))
    assert result.total == 0
    result = catalog.search(SearchQuery(q="trabalho -inclusiva", mode="text", page_size=100))
    assert all(
        "inclusiva" not in (item["title"] or "").casefold() for item in result.results
    )


def test_empty_query_friendly_error(catalog: CatalogSearch) -> None:
    with pytest.raises(SearchError, match="Digite"):
        catalog.search(SearchQuery(q="   ", mode="text"))


def test_invalid_parameters(catalog: CatalogSearch) -> None:
    with pytest.raises(SearchError):
        catalog.search(SearchQuery(q="x", mode="invalido"))
    with pytest.raises(SearchError):
        catalog.search(SearchQuery(q="x", page_size=101))
    with pytest.raises(SearchError):
        catalog.search(SearchQuery(q="x", year_min=2020, year_max=2000))


def test_without_embedder_falls_back_to_text(installed) -> None:
    catalog = CatalogSearch(installed["release"], None)
    result = catalog.search(SearchQuery(q="inclusiva", mode="hybrid"))
    assert result.mode_used == "text"
    assert "semantic_unavailable" in result.warnings
    assert result.total > 0


def test_embedding_failure_falls_back(installed) -> None:
    class BrokenEmbedder:
        name = "broken"
        dims_full = 768

        def ready(self) -> bool:
            return True

        def embed_queries(self, texts):
            raise RuntimeError("servidor fora do ar")

        def embed_documents(self, documents):
            raise RuntimeError("servidor fora do ar")

    catalog = CatalogSearch(installed["release"], BrokenEmbedder())
    result = catalog.search(SearchQuery(q="inclusiva", mode="hybrid"))
    assert result.mode_used == "text"
    assert "semantic_unavailable" in result.warnings


def test_get_and_similar(catalog: CatalogSearch) -> None:
    record = catalog.get("capes:0001")
    assert record["id"] == "capes:0001"
    assert record["abstract"]
    with pytest.raises(SearchError):
        catalog.get("nao-existe")
    similar = catalog.similar("capes:0001", k=5)
    assert 0 < len(similar) <= 5
    assert all(item["id"] != "capes:0001" for item in similar)
    assert all(item["matched_by"] == ["significado"] for item in similar)


def test_display_fields_are_truecased(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="inclusiva", mode="text", page_size=10))
    assert result.results
    for item in result.results:
        assert item["great_area"] in {"Ciências Humanas", "Ciências Exatas"}
    record = catalog.get("capes:0001")
    assert record["great_area"] in {"Ciências Humanas", "Ciências Exatas"}
    similar = catalog.similar("capes:0001", k=3)
    assert all(
        item["great_area"] in {"Ciências Humanas", "Ciências Exatas"}
        for item in similar
    )


def test_stats_and_global_facets(catalog: CatalogSearch) -> None:
    stats = catalog.stats()
    assert stats["records"] == 60
    assert stats["version"] == "2026.10.21"
    assert stats["years"] == [1990, 2024]
    assert stats["built_at"]
    facets = catalog.global_facets()
    assert {item["value"] for item in facets["uf"]} == {"MG", "SP", "RJ", "DF"}
    institution = facets["institution_acronym"][0]
    assert " — " in institution["label"]
    assert facets["year"]


def test_zero_vector_flag_and_thread_safety(installed) -> None:
    catalog = installed["catalog"]
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(5):
                result = catalog.search(SearchQuery(q="inclusiva", mode="hybrid"))
                assert result.total > 0
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []


def test_filters_are_applied_to_semantics(catalog: CatalogSearch) -> None:
    result = catalog.search(
        SearchQuery(q="educação inclusiva", mode="semantic", uf=("SP",), page_size=50)
    )
    assert result.results
    assert all(item["uf"] == "SP" for item in result.results)
    assert isinstance(result.results[0]["score"], float)


def test_parse_fts_query_drops_unquoted_stopwords() -> None:
    parsed = parse_fts_query("a educação no brasil")
    assert parsed.positives == ('"educação"', '"brasil"')
    assert parsed.negatives == ()


def test_parse_fts_query_keeps_quoted_stopwords() -> None:
    parsed = parse_fts_query('"não inclusiva" de')
    assert parsed.positives == ('"não inclusiva"',)


def test_parse_fts_query_keeps_stopwords_when_nothing_else_remains() -> None:
    parsed = parse_fts_query("de da")
    assert parsed.positives == ('"de"', '"da"')


def test_search_ignores_stopwords(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="a inclusiva no brasil", mode="text"))
    assert result.total > 0


def test_facet_labels_are_readable(catalog: CatalogSearch) -> None:
    facets = catalog.global_facets()
    great = {item["value"]: item["label"] for item in facets["great_area"]}
    assert great["CIENCIAS HUMANAS"] == "Ciências Humanas"
    institutions = {item["value"]: item["label"] for item in facets["institution_acronym"]}
    assert institutions["UFMG"] == "UFMG — Universidade Federal de Minas Gerais"
    scoped = catalog.search(SearchQuery(q="inclusiva", mode="text", page_size=50))
    scoped_great = {item["value"]: item["label"] for item in scoped.facets["great_area"]}
    assert scoped_great["CIENCIAS EXATAS"] == "Ciências Exatas"


def test_decade_facets_are_in_chronological_order(catalog: CatalogSearch) -> None:
    result = catalog.search(SearchQuery(q="inclusiva", mode="text", page_size=5))
    decades = [int(item["value"]) for item in result.facets["decade"]]
    assert len(decades) >= 2
    assert decades == sorted(decades)


def test_full_record_truecases_region_program_and_area(catalog: CatalogSearch) -> None:
    record = catalog.get("capes:0001")
    assert record["region"] == "Sudeste"
    assert record["program"] == "Programa de Pós-Graduação em Educação Inclusiva"
    assert record["knowledge_area"] == "Educação Inclusiva"


def test_similar_results_expose_cased_program_and_area(catalog: CatalogSearch) -> None:
    similar = catalog.similar("capes:0001", k=3)
    assert similar
    for entry in similar:
        assert entry["program"]
        assert "Programa de Pós-Graduação" in entry["program"]
        assert not entry["program"].isupper()
        assert entry["knowledge_area"]
        assert not entry["knowledge_area"].isupper()


def _write_v1_release(tmp_path: Path, dataset) -> Path:
    """Create a minimal format-1 release (plain text, float32, external FTS)."""
    release = tmp_path / "v1-release"
    release.mkdir()
    records = dataset.records[:12]
    connection = sqlite3.connect(release / CATALOG_DB)
    try:
        connection.execute(
            """
            CREATE TABLE theses(
                rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, year INTEGER,
                defense_date TEXT, title TEXT, author TEXT, advisors TEXT, degree TEXT,
                institution TEXT, institution_acronym TEXT, uf TEXT, region TEXT,
                program TEXT, program_code TEXT, great_area TEXT, knowledge_area TEXT,
                evaluation_area TEXT, concentration_area TEXT, research_line TEXT,
                keywords TEXT, keywords_en TEXT, abstract TEXT, abstract_en TEXT,
                language TEXT, pages INTEGER, library TEXT, url TEXT, source TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE theses_fts USING fts5(
                title, keywords, abstract, author, advisors, program, institution,
                content='theses', content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE facets(kind TEXT, value TEXT, label TEXT, count INTEGER)"
        )
        columns = (
            "id, year, defense_date, title, author, advisors, degree, institution, "
            "institution_acronym, uf, region, program, program_code, great_area, "
            "knowledge_area, evaluation_area, concentration_area, research_line, "
            "keywords, keywords_en, abstract, abstract_en, language, pages, library, url, source"
        )
        for record in records:
            connection.execute(
                f"INSERT INTO theses ({columns}) VALUES ({', '.join('?' for _ in range(27))})",
                [record.get(name) for name in columns.split(", ")],
            )
        connection.execute(
            "INSERT INTO theses_fts(rowid, title, keywords, abstract, author, advisors, "
            "program, institution) SELECT rowid, title, keywords, abstract, author, "
            "advisors, program, institution FROM theses"
        )
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            [
                ("version", dataset.version),
                ("records", str(len(records))),
                ("years", "1990,2024"),
                ("missing_vectors", "0"),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    vectors = np.stack(
        [
            dataset.embedder.vector_256(f"{row['title']} {row['abstract']}")
            for row in records
        ]
    ).astype(np.float32)
    np.save(release / VECTORS_FILE, vectors)
    return release


def test_format_one_release_remains_readable(tmp_path, installed) -> None:
    release = _write_v1_release(tmp_path, installed["dataset"])
    catalog = CatalogSearch(release, installed["dataset"].embedder)
    text = catalog.search(SearchQuery(q="inclusiva", mode="text", page_size=10))
    assert text.total > 0
    assert any("[[" in item["snippet"] for item in text.results)
    record = catalog.get("capes:0001")
    assert record["abstract"].startswith("Este trabalho")
    semantic = catalog.search(SearchQuery(q="educação inclusiva", mode="semantic"))
    assert semantic.mode_used == "semantic"
    assert semantic.results
    assert all(item["matched_by"] == ["significado"] for item in semantic.results)


def test_get_and_similar_apply_display_textcase(catalog: CatalogSearch) -> None:
    item = catalog.get("capes:0001")
    assert item["great_area"] in {"Ciências Humanas", "Ciências Exatas"}
    similar = catalog.similar("capes:0001", k=3)
    assert similar
    assert all(
        entry["great_area"] in {"Ciências Humanas", "Ciências Exatas"}
        for entry in similar
    )
