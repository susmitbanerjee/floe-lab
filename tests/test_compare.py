"""Table-driven tests for the expected-answer comparator (checks.compare)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from checks.compare import (
    cells_equal,
    compare,
    has_order_by,
    read_csv,
    render,
    write_csv,
)


@pytest.mark.parametrize(
    ("expected", "actual", "equal"),
    [
        ("12", "12", True),
        ("12", "13", False),
        ("12.5", "12.50", True),  # decimal scale differs
        ("12.50", "12.5", True),
        ("12.50", "12.51", True),  # within 0.01
        ("12.50", "12.52", False),
        ("12", "12.0", True),  # one side fractional: tolerance applies
        ("100000000000000001", "100000000000000000", False),  # integers compare exactly
        ("0.0499", "0.05", True),
        ("1.5e3", "1500.004", True),
        ("-3.25", "-3.26", True),
        ("abc", "abc", True),
        ("abc", "ABC", False),  # strings exact, case included
        ("", "", True),  # NULL equals NULL
        ("", "0", False),  # NULL is not zero
        ("0", "", False),
        ("", "abc", False),
        ("1995-03-05", "1995-03-05", True),
        ("nan", "nan", True),  # same text
        ("inf", "1e400", False),  # non-finite never compares numerically
    ],
)
def test_cells(expected: str, actual: str, equal: bool) -> None:
    assert cells_equal(expected, actual) is equal


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        (12, "12"),
        (Decimal("12.50"), "12.50"),
        (0.1, "0.1"),
        ("Delhi NCR", "Delhi NCR"),
        (date(2026, 11, 18), "2026-11-18"),
        (True, "true"),
    ],
)
def test_render(value: object, text: str) -> None:
    assert render(value) == text


@pytest.mark.parametrize(
    ("sql", "ordered"),
    [
        ("SELECT a FROM t ORDER BY a", True),
        ("select a from t\norder   by a desc", True),
        ("SELECT a FROM t", False),
        ("-- ORDER BY in a comment\nSELECT a FROM t", False),
        ("/* order by */ SELECT a FROM t", False),
        ("SELECT border FROM t", False),
    ],
)
def test_has_order_by(sql: str, ordered: bool) -> None:
    assert has_order_by(sql) is ordered


ROWS = [["Mumbai", "3", "12.50"], ["Pune", "2", "7.00"], ["Chennai", "1", ""]]


@pytest.mark.parametrize(
    ("actual", "ordered", "problem"),
    [
        (ROWS, True, None),
        ([["Mumbai", "3", "12.5"], ["Pune", "2", "7"], ["Chennai", "1", ""]], True, None),
        (list(reversed(ROWS)), False, None),  # order ignored without ORDER BY
        (list(reversed(ROWS)), True, "row 1"),  # order matters with ORDER BY
        (ROWS[:2], True, "2 rows, expected 3"),
        ([["Mumbai", "3", "12.50"], ["Pune", "2", "7.00"], ["Chennai", "1", "0"]], True, "row 3"),
        ([["Mumbai", "3", "12.50"], ["Pune", "2", "7.03"], ["Chennai", "1", ""]], False, "row"),
        ([["Mumbai", "3"], ["Pune", "2", "7.00"], ["Chennai", "1", ""]], True, "row 1"),
    ],
)
def test_compare_rows(actual: list[list[str]], ordered: bool, problem: str | None) -> None:
    result = compare(ROWS, actual, ordered)
    if problem is None:
        assert result is None
    else:
        assert result is not None
        assert problem in result


def test_unordered_rows_pair_up_despite_scale_differences() -> None:
    expected = [["a", "12.50"], ["a", "12.40"]]
    assert compare(expected, [["a", "12.4"], ["a", "12.5"]], ordered=False) is None


def test_csv_round_trip_keeps_nulls_commas_and_scale(tmp_path: Path) -> None:
    path = tmp_path / "answer.csv"
    rows = [["a, b", "12.50", ""], ["", "1", "x"]]
    write_csv(path, ["text", "amount", "maybe"], rows)
    assert read_csv(path) == (["text", "amount", "maybe"], rows)
    assert path.read_text(encoding="utf-8").splitlines()[1] == '"a, b",12.50,'
