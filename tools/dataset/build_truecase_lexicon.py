#!/usr/bin/env python3
"""Build the truecasing lexicon from the normalised CAPES metadata.

The catalogue has two very different casing regimes: records up to roughly
2012 mix sentence case and uppercase, while records from 2013 onwards (and
1987-1999) are almost entirely uppercase.  Non-shouting texts therefore provide
the evidence needed to learn acronyms, proper names and accents; that evidence
is aggregated here and written to ``src/buscateses/data/truecase.json``.

The output is intentionally small (fewer than 40 thousand entries and under
1.5 MB) because it ships with the application and is loaded lazily.

Examples
--------
    .venv/bin/python tools/dataset/build_truecase_lexicon.py \
        --metadata tools/dataset/work/metadata
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from buscateses.textcase import is_shouting  # noqa: E402

LOGGER = logging.getLogger("truecase-lexicon")

MIN_TOTAL = 15
MIN_ACRONYM_RATIO = 0.85
ACRONYM_MAX_LETTERS = 10
ACCENT_MIN_TOTAL = 50
ACCENT_MIN_RATIO = 0.95
MAX_ENTRIES = 40_000
MAX_BYTES = 1_500_000
ACRONYM_LIMIT = 6_000
PROPER_LIMIT = 10_000
ACCENT_LIMIT = 24_000

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_END_RE = re.compile(r"[.!?]")

# Portuguese function words: capitalised occurrences of these are never
# interesting as proper names (and often come from title-case headings).
_STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de da do das dos em no na nos nas por pela pelo
    pelas pelos para com sem sob sobre entre e ou que se ao aos a as como
    quando onde qual quais cujo cuja cujos cujas seu sua seus suas este esta
    estes estas esse essa esses essas aquele aquela aqueles aquelas isto isso
    aquilo eu tu ele ela eles elas nos vos lhe lhes me te se mais menos muito
    muita muitos muitas pouco pouca poucos poucas tambem nao sim ja ainda so
    apenas cada todo toda todos todas outro outra outros outras mesmo mesma
    mesmos mesmas ser estar ter haver fazer poder dever ir vir dar ver dizer
    saber querer ficar passar deixar levar trazer parecer ha ate apos atraves
    durante mediante conforme segundo contra desde federal estadual municipal
    nacional universidade instituto faculdade escola centro departamento
    programa curso ensino pesquisa ciencia ciencias saude educacao estado
    municipio sociedade associacao fundacao empresa
    """.split()
)

# A handful of Brazilian acronyms that barely appear in the pre-2013
# mixed-case slice but are ubiquitous in the uppercase years.  Keeping them
# here avoids lowercasing them in titles and abstracts.
SEED_ACRONYMS = frozenset(
    {
        "bncc",
        "capes",
        "cnpj",
        "cnpq",
        "covid",
        "enem",
        "fapesp",
        "fiocruz",
        "ibge",
        "lgbtqia",
        "pibid",
        "pnld",
        "sus",
        "uerj",
        "ufba",
        "ufc",
        "ufmt",
        "ufpe",
        "ufpr",
        "ufrgs",
        "ufrj",
        "ufsc",
        "ufmg",
        "unb",
        "unicamp",
        "unesco",
        "unesp",
        "unicef",
        "unipampa",
        "usp",
    }
)

# Proper names whose conventional spelling is not recoverable from the
# pre-2013 evidence alone (they became common only in the uppercase years).
SEED_PROPER = {
    "libras": "Libras",
    "pampa": "Pampa",
    "pinus": "Pinus",
}

# Labels occasionally leaked into metadata fields; they are not acronyms.
_ACRONYM_BLOCKLIST = frozenset(
    {"abstract", "keywords", "resumo", "titulo", "autor", "orientador", "tese"}
)

# Statistically ambiguous pairs explicitly excluded by the specification.
_ACCENT_BLOCKLIST = frozenset({"e", "esta", "publica"})


def _fold(text: str) -> str:
    """Return lowercase text without diacritics."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def _has_accent(text: str) -> bool:
    """Return True if *text* contains at least one accented letter."""
    for char in text:
        if not char.isalpha():
            continue
        decomposed = unicodedata.normalize("NFD", char)
        if any(unicodedata.combining(part) for part in decomposed[1:]):
            return True
    return False


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _iter_words(text: str):
    """Yield ``(word, at_sentence_start)`` pairs for *text*."""
    cursor = 0
    first = True
    for match in _WORD_RE.finditer(text):
        between = text[cursor : match.start()]
        sentence_start = first or bool(_SENTENCE_END_RE.search(between))
        yield match.group(0), sentence_start
        cursor = match.end()
        first = False


def _process_field(
    field: str | None,
    counts: dict[str, list[int]],
    accented_totals: Counter[str],
    accented_forms: Counter[str],
    capitalized_forms: Counter[str],
) -> None:
    """Accumulate casing and accent evidence from one metadata field."""
    if not field:
        return
    shouting = is_shouting(field)
    has_accent = _has_accent(field)
    if shouting and not has_accent:
        return
    for word, sentence_start in _iter_words(field):
        if any(char.isdigit() for char in word):
            continue
        key = _fold(word)
        if not shouting:
            if len(word) >= 2 and word.isupper():
                counts.setdefault(key, [0, 0, 0])[0] += 1
            elif word.islower():
                counts.setdefault(key, [0, 0, 0])[2] += 1
            elif not sentence_start and word[0].isupper():
                counts.setdefault(key, [0, 0, 0])[1] += 1
                capitalized_forms[word] += 1
            # Sentence-initial capitals and mixed forms carry no evidence.
        if has_accent:
            accented_totals[key] += 1
            if _has_accent(word):
                accented_forms[word.casefold()] += 1


def _select_acronyms(counts: dict[str, list[int]]) -> dict[str, int]:
    """Return ``key -> uppercase count`` for acronym candidates."""
    selected: dict[str, int] = {}
    for key, (upper, _capitalized, lower) in counts.items():
        if not 2 <= len(key) <= ACRONYM_MAX_LETTERS:
            continue
        if key in _ACRONYM_BLOCKLIST:
            continue
        total = upper + _capitalized + lower
        if total >= MIN_TOTAL and upper / total >= MIN_ACRONYM_RATIO:
            selected[key] = upper
    for key in SEED_ACRONYMS:
        selected.setdefault(key, 0)
    return selected


def _select_proper(counts: dict[str, list[int]]) -> dict[str, int]:
    """Return ``key -> capitalized count`` for proper-name candidates."""
    selected: dict[str, int] = {}
    for key, (_upper, capitalized, lower) in counts.items():
        if len(key) < 2 or key in _STOPWORDS:
            continue
        total = _upper + capitalized + lower
        if total >= MIN_TOTAL and capitalized / total >= MIN_ACRONYM_RATIO:
            selected[key] = capitalized
    return selected


def _select_accents(
    totals: Counter[str],
    forms: Counter[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """Return ``(unaccented key -> accented form, key -> evidence count)``."""
    best: dict[str, tuple[str, int]] = {}
    for surface, count in forms.items():
        key = _fold(surface)
        if len(key) < 2 or key in _ACCENT_BLOCKLIST:
            continue
        current = best.get(key)
        if (
            current is None
            or count > current[1]
            or (count == current[1] and surface < current[0])
        ):
            best[key] = (surface, count)
    selected: dict[str, str] = {}
    scores: dict[str, int] = {}
    for key, (surface, count) in best.items():
        total = totals.get(key, 0)
        if total < ACCENT_MIN_TOTAL or surface == key:
            continue
        if count / total >= ACCENT_MIN_RATIO:
            selected[key] = surface
            scores[key] = count
    return selected, scores


def _top(
    entries: dict[str, Any],
    limit: int,
    scores: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Keep the *limit* highest-scoring entries (ties by key).

    When *scores* is given, ``entries`` only carries display values.
    """
    if len(entries) <= limit:
        return entries
    score_lookup = scores if scores is not None else entries
    ranked = sorted(entries, key=lambda key: (-score_lookup.get(key, 0), key))
    return {key: entries[key] for key in ranked[:limit]}


def _acronym_upper(key: str) -> str:
    """Return the display form of an acronym."""
    return key.upper()


def _best_capitalized_forms(capitalized_forms: Counter[str]) -> dict[str, str]:
    """Map every folded key to its most frequent capitalised surface form."""
    best: dict[str, tuple[str, int]] = {}
    for form, count in capitalized_forms.items():
        key = _fold(form)
        current = best.get(key)
        if current is None or count > current[1] or (
            count == current[1] and form < current[0]
        ):
            best[key] = (form, count)
    return {key: form for key, (form, _count) in best.items()}


def _institution_acronyms() -> set[str]:
    """Return the curated institution acronyms kept next to this script."""
    path = Path(__file__).with_name("institution_acronyms.txt")
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.strip().upper() for line in lines if line.strip() and not line.startswith("#")}


def _build_payload(
    acronyms: dict[str, int],
    proper: dict[str, int],
    accents: dict[str, str],
    accent_scores: dict[str, int],
    capitalized_forms: Counter[str],
) -> dict[str, Any]:
    """Assemble the final JSON-compatible lexicon."""
    chosen_acronyms = _top(acronyms, ACRONYM_LIMIT)
    chosen_proper = _top(proper, PROPER_LIMIT)
    chosen_accents = _top(accents, ACCENT_LIMIT, accent_scores)
    best_forms = _best_capitalized_forms(capitalized_forms)

    proper_values: dict[str, str] = {}
    for key in chosen_proper:
        surface = best_forms.get(key)
        if surface is None:
            surface = chosen_accents.get(key, key)
            surface = surface[:1].upper() + surface[1:]
        proper_values[key] = surface
    for key, value in SEED_PROPER.items():
        proper_values.setdefault(key, value)

    payload: dict[str, Any] = {
        "version": 1,
        "acronyms": sorted(
            {_acronym_upper(key) for key in chosen_acronyms} | _institution_acronyms()
        ),
        "proper": dict(sorted(proper_values.items())),
        "accents": dict(sorted(chosen_accents.items())),
    }
    return payload


def _serialize(payload: dict[str, Any]) -> bytes:
    """Serialize the lexicon compactly with sorted keys."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _enforce_size(
    payload: dict[str, Any],
    *,
    proper_scores: dict[str, int],
    accent_scores: dict[str, int],
) -> dict[str, Any]:
    """Trim the least frequent entries until both output limits are met."""

    def total_entries() -> int:
        return (
            len(payload["acronyms"])
            + len(payload["proper"])
            + len(payload["accents"])
        )

    def drop(name: str, count: int) -> int:
        if name == "acronyms":
            values = payload["acronyms"]
            removable = sorted(values)[:count]
            for key in removable:
                values.remove(key)
            return len(removable)
        values = payload[name]
        scores = accent_scores if name == "accents" else proper_scores
        removable = sorted(values, key=lambda key: (scores.get(key, 0), key))[:count]
        for key in removable:
            values.pop(key, None)
        return len(removable)

    while total_entries() > MAX_ENTRIES:
        removed = 0
        for name in ("accents", "proper", "acronyms"):
            if total_entries() <= MAX_ENTRIES:
                break
            removed += drop(name, max(1, total_entries() - MAX_ENTRIES))
        if not removed:
            break

    while len(_serialize(payload)) > MAX_BYTES:
        removed = 0
        for name in ("accents", "proper", "acronyms"):
            if len(_serialize(payload)) <= MAX_BYTES:
                return payload
            removed += drop(name, max(1, len(payload[name]) // 50))
        if not removed:
            break
    return payload


def build_lexicon(
    metadata_dir: Path,
    *,
    sample_modulo: int = 3,
    max_texts: int = 600_000,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Read the metadata and return ``(payload, statistics)``."""
    parts = sorted(Path(metadata_dir).glob("*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Nenhuma parte Parquet em {metadata_dir}")

    counts: dict[str, list[int]] = {}
    accented_totals: Counter[str] = Counter()
    accented_forms: Counter[str] = Counter()
    capitalized_forms: Counter[str] = Counter()

    connection = duckdb.connect()
    connection.execute("SET threads=4")
    connection.execute("SET memory_limit='4GB'")
    query = """
        SELECT title, abstract, keywords
        FROM read_parquet(?)
        WHERE (hash(id) % ?) = 0
    """
    cursor = connection.execute(query, [[str(path) for path in parts], sample_modulo])
    seen = 0
    fields = 0
    while True:
        rows = cursor.fetchmany(8192)
        if not rows:
            break
        for title, abstract, keywords in rows:
            for field in (title, abstract, keywords):
                if field:
                    fields += 1
                    _process_field(
                        field,
                        counts,
                        accented_totals,
                        accented_forms,
                        capitalized_forms,
                    )
            seen += 1
        if seen >= max_texts:
            LOGGER.info("Amostra máxima de %d textos atingida", max_texts)
            break
        if seen and seen % 50_000 < 8192:
            LOGGER.info("Processados %d textos (%d campos)", seen, fields)
    connection.close()

    acronyms = _select_acronyms(counts)
    proper = _select_proper(counts)
    accents, accent_scores = _select_accents(accented_totals, accented_forms)
    payload = _build_payload(
        acronyms, proper, accents, accent_scores, capitalized_forms
    )
    payload = _enforce_size(
        payload, proper_scores=proper, accent_scores=accent_scores
    )
    statistics = {
        "texts": seen,
        "fields": fields,
        "word_types": len(counts),
        "accented_word_types": len(accented_totals),
        "acronyms": len(payload["acronyms"]),
        "proper": len(payload["proper"]),
        "accents": len(payload["accents"]),
        "entries": (
            len(payload["acronyms"]) + len(payload["proper"]) + len(payload["accents"])
        ),
        "bytes": len(_serialize(payload)),
    }
    return payload, statistics


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    _setup_logging()
    parser = argparse.ArgumentParser(description="Gera o léxico de truecasing")
    parser.add_argument(
        "--metadata",
        type=Path,
        default=REPO_ROOT / "tools" / "dataset" / "work" / "metadata",
        help="diretório com as partes Parquet normalizadas",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SRC_DIR / "buscateses" / "data" / "truecase.json",
        help="arquivo JSON de saída",
    )
    parser.add_argument(
        "--sample-modulo",
        type=int,
        default=3,
        help="processa 1 a cada N textos (amostragem determinística)",
    )
    parser.add_argument(
        "--max-texts",
        type=int,
        default=600_000,
        help="limite de textos processados",
    )
    arguments = parser.parse_args(argv)
    if arguments.sample_modulo < 1:
        parser.error("--sample-modulo deve ser >= 1")

    payload, statistics = build_lexicon(
        arguments.metadata,
        sample_modulo=arguments.sample_modulo,
        max_texts=arguments.max_texts,
    )
    data = _serialize(payload)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = arguments.out.with_suffix(".json.tmp")
    temporary.write_bytes(data)
    temporary.replace(arguments.out)
    LOGGER.info("Léxico gravado em %s", arguments.out)
    for key, value in statistics.items():
        LOGGER.info("  %-22s %s", key, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
