"""Progressive FTS relaxation driven by document frequency.

When an ``AND`` query returns nothing, the terms that occur in the most
documents are dropped first.  Dropping a common word usually makes the query
both more selective and much cheaper than falling back to a plain ``OR`` over
every term, which can scan hundreds of thousands of postings.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Sequence

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
#: Name of the temporary ``fts5vocab`` table created on each connection.
VOCAB_TABLE = "buscateses_vocab"


def create_vocabulary(connection: sqlite3.Connection, fts_table: str) -> bool:
    """Create the per-connection ``fts5vocab`` helper table.

    Returns ``False`` when the SQLite build lacks the ``fts5vocab`` module (or
    the FTS table does not exist yet); callers then simply skip relaxation by
    frequency.
    """
    try:
        connection.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.{VOCAB_TABLE} "
            f"USING fts5vocab(main, '{fts_table}', 'row')"
        )
    except sqlite3.Error:
        return False
    return True


def fold_term(token: str) -> str:
    """Fold *token* the same way the ``unicode61`` tokenizer does."""
    decomposed = unicodedata.normalize("NFD", token.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def raw_term(expression: str) -> str:
    """Recover the literal text of an FTS expression built by the app.

    ``title : "educação inclusiva"`` becomes ``educação inclusiva``.  Only
    expressions produced by :func:`buscateses.catalog.search.parse_fts_query`
    are expected here; raw user syntax is never evaluated.
    """
    text = expression
    if " : " in text:
        text = text.split(" : ", 1)[1]
    text = text.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('""', '"')
    return text


def document_frequencies(
    connection: sqlite3.Connection,
    expressions: Sequence[str],
) -> list[int]:
    """Return the document frequency of each expression's rarest token.

    A multi-word phrase is already conjunctive; the frequency of its rarest
    token is the best proxy for how selective the whole phrase is.  Missing
    tokens count as zero (extremely rare).
    """
    tokens_by_expression: list[list[str]] = []
    unique: set[str] = set()
    for expression in expressions:
        words = [
            fold_term(token)
            for token in _TOKEN_RE.findall(raw_term(expression))
            if token
        ]
        tokens_by_expression.append(words)
        unique.update(words)
    if not unique:
        return [0 for _ in expressions]
    placeholders = ",".join("?" for _ in unique)
    try:
        rows = connection.execute(
            f"SELECT term, doc FROM temp.{VOCAB_TABLE} WHERE term IN ({placeholders})",
            list(unique),
        ).fetchall()
    except sqlite3.Error:
        return [0 for _ in expressions]
    frequencies = {str(row[0]): int(row[1]) for row in rows}
    return [
        min((frequencies.get(word, 0) for word in words), default=0)
        for words in tokens_by_expression
    ]


def relaxation_drops(frequencies: Sequence[int], min_terms: int = 2) -> list[int]:
    """Return expression indices in the order they should be dropped.

    The most frequent expression goes first.  At least ``min_terms`` terms are
    kept so a relaxed query does not collapse into a single very broad word.
    """
    remaining = list(range(len(frequencies)))
    order: list[int] = []
    floor = max(1, int(min_terms))
    while len(remaining) > floor:
        worst = max(remaining, key=lambda index: (frequencies[index], index))
        order.append(worst)
        remaining.remove(worst)
    return order


__all__ = [
    "VOCAB_TABLE",
    "create_vocabulary",
    "document_frequencies",
    "fold_term",
    "raw_term",
    "relaxation_drops",
]
