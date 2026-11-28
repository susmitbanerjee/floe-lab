"""Tests for checks.run_floe with a fake database (no Floe, no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from checks.compare import write_csv
from checks.run_floe import Outcome, QueryError, check_query, files_scanned

SQL = "SELECT region, count(*) AS count, sum(amount) AS sum FROM t GROUP BY region ORDER BY region;"


class FakeDatabase:
    def __init__(self, answers: dict[str, list[tuple[Any, ...]] | QueryError]) -> None:
        self.answers = answers
        self.seen: list[str] = []

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        self.seen.append(sql)
        answer = self.answers["explain" if sql.startswith("EXPLAIN ANALYZE") else "query"]
        if isinstance(answer, QueryError):
            raise answer
        return answer


@pytest.fixture
def expected(tmp_path: Path) -> Path:
    write_csv(tmp_path / "region_7d.csv", ["region", "count", "sum"], [["Mumbai", "3", "12.50"]])
    files = {"queries": {"region_7d": {"data_files": 57}}}
    (tmp_path / "files.json").write_text(json.dumps(files))
    (tmp_path / "region_7d.sql").write_text(SQL)
    return tmp_path


def run(expected: Path, query: Any, explain: Any = None) -> list[Outcome]:
    plan = explain if explain is not None else [("ParquetExec: files_scanned=57 bytes=1",)]
    db = FakeDatabase({"query": query, "explain": plan})
    return check_query(db, expected / "region_7d.sql", expected)


def test_matching_answer_and_file_count(expected: Path) -> None:
    outcomes = run(expected, [("Mumbai", 3, Decimal("12.5"))])
    assert [(o.status, o.query) for o in outcomes] == [
        ("ok", "region_7d"),
        ("ok", "region_7d files_scanned"),
    ]


def test_differing_row_is_reported(expected: Path) -> None:
    outcome = run(expected, [("Mumbai", 4, Decimal("12.5"))])[0]
    assert outcome.status == "fail"
    assert "row 1" in outcome.detail
    assert outcome.line().startswith("fail  region_7d: row 1")


def test_query_error_carries_sqlstate(expected: Path) -> None:
    (outcome,) = run(expected, QueryError("42P01", 'relation "t" does not exist'))
    assert outcome.status == "error"
    assert "SQLSTATE 42P01" in outcome.detail


@pytest.mark.parametrize(
    ("plan", "status", "detail"),
    [
        ([("files_scanned=57",)], "ok", "57 = files in range"),
        ([("files_scanned=40",)], "ok", "40 < 57"),
        ([("files_scanned=58",)], "fail", "more than the 57"),
        ([("no metrics here",)], "fail", "no files_scanned"),
    ],
)
def test_files_scanned_rules(
    expected: Path, plan: list[tuple[Any, ...]], status: str, detail: str
) -> None:
    outcome = run(expected, [("Mumbai", 3, Decimal("12.50"))], plan)[1]
    assert outcome.status == status
    assert detail in outcome.detail


def test_explain_wraps_the_query_without_its_semicolon(expected: Path) -> None:
    db = FakeDatabase(
        {"query": [("Mumbai", 3, Decimal("12.5"))], "explain": [("files_scanned=57",)]}
    )
    check_query(db, expected / "region_7d.sql", expected)
    assert db.seen[1] == "EXPLAIN ANALYZE " + SQL.rstrip(";")


def test_files_scanned_reads_the_first_metric() -> None:
    assert files_scanned([("a files_scanned=3", None), ("files_scanned=9",)]) == 3
    assert files_scanned([]) is None
