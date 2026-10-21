"""Offline tests for the recent-years harvester (no network calls)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import harvest_recent
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_RANGE = re.compile(
    r'^(?P<field>[^:]+):\[(?P<low>\*|"[^"]*") TO (?P<high>\*|"[^"]*")\]$'
)
_PREFIX = re.compile(r"^(?P<field>[^:]+):(?P<prefix>.*)\*$")
_EQUALITY = re.compile(r'^(?P<field>[^:]+):"(?P<value>.*)"$')


def _filters(params: list[tuple[str, object]]) -> list[str]:
    return [str(value) for key, value in params if key == "filter[]"]


def _values(record: dict[str, object], field: str) -> list[str]:
    value = record.get(field)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _matches(filters: list[str], record: dict[str, object]) -> bool:
    for expression in filters:
        if expression.startswith("(") and expression.endswith(")"):
            if not all(
                _matches([part], record)
                for part in expression[1:-1].split(" AND ")
            ):
                return False
        elif match := _RANGE.fullmatch(expression):
            low = None if match["low"] == "*" else match["low"].strip('"')
            high = None if match["high"] == "*" else match["high"].strip('"')
            values = _values(record, match["field"])
            if not any(
                (low is None or value >= low) and (high is None or value <= high)
                for value in values
            ):
                return False
        elif match := _PREFIX.fullmatch(expression):
            prefix = re.sub(r"\\(.)", r"\1", match["prefix"])
            if not any(value.startswith(prefix) for value in _values(record, match["field"])):
                return False
        elif match := _EQUALITY.fullmatch(expression):
            if match["value"] not in _values(record, match["field"]):
                return False
        else:
            return False
    return True


class FakeApi:
    """Minimal stand-in for the BDTD and CAPES HTTP endpoints."""

    def __init__(
        self,
        bdtd_records: list[dict[str, object]],
        capes_records: list[dict[str, object]],
    ) -> None:
        self.bdtd_records = bdtd_records
        self.capes_records = capes_records
        self.calls = 0

    def _bdtd_matching(self, filters: list[str]) -> list[dict[str, object]]:
        return [record for record in self.bdtd_records if _matches(filters, record)]

    def __call__(
        self,
        session: object,
        method: str,
        url: str,
        *,
        params: list[tuple[str, object]] | None = None,
        body: dict[str, object] | None = None,
        limiter: object,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del session, method, url, limiter, headers
        self.calls += 1
        if body is not None:
            page = int(body.get("pagina") or 1)
            size = 20
            start = (page - 1) * size
            return {
                "total": len(self.capes_records),
                "registrosPorPagina": size,
                "tesesDissertacoes": self.capes_records[start : start + size],
            }
        assert params is not None
        filters = _filters(params)
        matching = self._bdtd_matching(filters)
        options = dict(params)
        limit = int(options.get("limit") or 1)
        page = int(options.get("page") or 1)
        start = (page - 1) * limit
        records = [{"rawData": record} for record in matching[start : start + limit]]
        return {"resultCount": len(matching), "records": records}


def _bdtd_record(
    identifier: str,
    institution: str,
    format_: str,
    *,
    languages: tuple[str, ...] = ("por",),
    author: str | None = None,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": identifier,
        "instname_str": institution,
        "format": [format_],
        "title": f"Título {identifier}",
        "publishDate": ["2025"],
        "language": list(languages),
    }
    if author is not None:
        raw["author"] = [author]
    return raw


def _capes_record(identifier: str) -> dict[str, object]:
    return {"id": identifier, "titulo": f"Título {identifier}", "dataDefesa": "2025-03-01"}


def _api() -> FakeApi:
    records = [
        *(
            _bdtd_record(
                f"a{index}", "Universidade A", "masterThesis", author=f"Autor {index}"
            )
            for index in range(600)
        ),
        *(
            _bdtd_record(
                f"b{index}", "Universidade A", "doctoralThesis", author=f"Autor {index}"
            )
            for index in range(500)
        ),
        *(
            _bdtd_record(f"c{index}", "Universidade B", "masterThesis", author=f"Autor {index}")
            for index in range(150)
        ),
    ]
    capes = [_capes_record(f"capes{index}") for index in range(45)]
    return FakeApi(records, capes)


def _install(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    monkeypatch.setattr(harvest_recent, "_request", api)
    monkeypatch.setattr(harvest_recent.RateLimiter, "wait", lambda self: None)


def test_bdtd_partitions_split_by_id_prefix_until_every_cell_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    _install(monkeypatch, api)
    partitions = harvest_recent.bdtd_partitions(
        object(), 2025, 1250, harvest_recent.RateLimiter(2)  # type: ignore[arg-type]
    )

    assert sum(partition.expected for partition in partitions) == 1250
    assert len(partitions) > 1
    for partition in partitions:
        assert partition.expected <= harvest_recent.BDTD_OFFSET_LIMIT
        # One constraint besides publishDate: the API answers 429 otherwise.
        assert len(partition.filters) == 1
        assert partition.filters[0].startswith("id:")
        matched = sum(1 for record in api.bdtd_records if _matches(partition.filters, record))
        assert matched == partition.expected
    names = [partition.name for partition in partitions]
    assert len(names) == len(set(names))
    assert len({name.lower() for name in names}) == len(names)
    assert all(character.isalnum() or character in "-~" for name in names for character in name)


def test_id_prefixes_recurse_and_escape_special_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _bdtd_record(f"UNB-41_{index:05x}", "Universidade Única", "masterThesis")
        for index in range(2600)
    ]
    api = FakeApi(records, [])
    _install(monkeypatch, api)
    partitions = harvest_recent.bdtd_partitions(
        object(), 2025, 2600, harvest_recent.RateLimiter(2)  # type: ignore[arg-type]
    )

    assert sum(partition.expected for partition in partitions) == 2600
    assert any("\\-" in partition.filters[0] for partition in partitions)
    for partition in partitions:
        assert partition.expected <= harvest_recent.BDTD_OFFSET_LIMIT
        assert (
            sum(1 for record in api.bdtd_records if _matches(partition.filters, record))
            == partition.expected
        )


def test_bdtd_partitions_fail_when_prefixes_do_not_cover_the_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_bdtd_record(f"x{index}", "Universidade Única", "masterThesis") for index in range(1500)]
    _install(monkeypatch, FakeApi(records, []))
    with pytest.raises(harvest_recent.HarvestError, match="cobre"):
        harvest_recent.bdtd_partitions(
            object(), 2025, 1600, harvest_recent.RateLimiter(2)  # type: ignore[arg-type]
        )


def test_harvest_caches_pages_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api()
    _install(monkeypatch, api)
    out = tmp_path / "raw"

    report = harvest_recent.harvest(
        out, [2025], sources=("bdtd", "capes"), rate=2
    )

    bdtd_year = report["sources"]["bdtd"]["years"]["2025"]
    assert bdtd_year["expected_total"] == 1250
    assert bdtd_year["records_collected_unique"] == 1250
    assert bdtd_year["records_duplicated_across_partitions"] == 0
    assert bdtd_year["complete"] is True
    assert len(bdtd_year["partitions"]) > 1
    for name, partition in bdtd_year["partitions"].items():
        assert partition["filters"]
        assert partition["complete"] is True
        assert partition["records_collected"] == partition["expected"]
        pages = list((out / "bdtd" / "2025" / name).glob("page-*.json.gz"))
        assert len(pages) == partition["pages_total"]

    plan = json.loads((out / "bdtd" / "2025" / "_plan.json").read_text(encoding="utf-8"))
    assert plan["version"] == harvest_recent.BDTD_PLAN_VERSION
    assert plan["expected_total"] == 1250
    assert {item["name"] for item in plan["partitions"]} == set(bdtd_year["partitions"])

    capes_year = report["sources"]["capes"]["years"]["2025"]
    assert capes_year["expected_total"] == 45
    assert capes_year["records_collected_unique"] == 45
    assert capes_year["complete"] is True

    stored = json.loads((out / "_sources_recent.json").read_text(encoding="utf-8"))
    assert stored == report
    assert stored["rate_limit_per_second"] == 2
    assert stored["sources"]["bdtd"]["url"] == harvest_recent.BDTD_URL

    cached_report = harvest_recent.harvest(
        out, [2025], sources=("bdtd", "capes"), rate=2
    )
    for partition in cached_report["sources"]["bdtd"]["years"]["2025"]["partitions"].values():
        assert partition["pages_fetched_now"] == 0
        assert partition["pages_cached"] == partition["pages_total"]
    assert cached_report["sources"]["bdtd"]["years"]["2025"]["complete"] is True


def test_harvest_removes_stale_partition_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api()
    _install(monkeypatch, api)
    out = tmp_path / "raw"
    stale = out / "bdtd" / "2025" / "format-masterThesis"
    stale.mkdir(parents=True)
    (stale / "page-00001.json.gz").write_bytes(b"old")

    report = harvest_recent.harvest(out, [2025], sources=("bdtd",), rate=2)

    assert not stale.exists()
    expected = set(report["sources"]["bdtd"]["years"]["2025"]["partitions"])
    names = {path.name for path in (out / "bdtd" / "2025").iterdir() if path.is_dir()}
    assert names == expected


def test_harvest_max_pages_marks_year_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api()
    _install(monkeypatch, api)
    report = harvest_recent.harvest(
        tmp_path / "raw", [2025], sources=("bdtd", "capes"), max_pages=1, rate=2
    )
    bdtd_year = report["sources"]["bdtd"]["years"]["2025"]
    assert bdtd_year["complete"] is False
    assert bdtd_year["partitions"][next(iter(bdtd_year["partitions"]))]["pages_total"] == 1
    assert report["sources"]["capes"]["years"]["2025"]["complete"] is False


def test_default_years_uses_last_metadata_year(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    table = pa.table({"year": pa.array([2023, 2024], type=pa.int16())})
    pq.write_table(table, metadata / "part-000.parquet")

    assert harvest_recent.default_years(metadata, 2026) == [2025, 2026]
    assert harvest_recent.default_years(metadata, 2024) == [2024]

    with pytest.raises(FileNotFoundError):
        harvest_recent.default_years(tmp_path / "missing", 2026)


def test_corrupt_cached_page_is_removed(tmp_path: Path) -> None:
    path = tmp_path / "page-00001.json.gz"
    path.write_bytes(b"not gzip")
    assert harvest_recent._load_page(path) is None
    assert not path.exists()
