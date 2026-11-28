"""Compare query results with expected CSV answers (docs/adr/0003-expected-answers.md).

Rules: integers and strings compare exactly; a pair of numbers where either has a fractional
part or exponent compares within 0.01 (so 12.5 equals 12.50); an empty field is NULL. Rows are
compared in order only when the query has ORDER BY, otherwise as sorted multisets.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

TOLERANCE = Decimal("0.01")
NULL = ""
Row = list[str]

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_ORDER_BY = re.compile(r"\border\s+by\b", re.I)
_INTEGER = re.compile(r"[+-]?\d+")


def render(value: object) -> str:
    """A Python value as expected-answer text."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def has_order_by(sql: str) -> bool:
    return bool(_ORDER_BY.search(_COMMENT.sub(" ", sql)))


def _number(text: str) -> Decimal | None:
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def cells_equal(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if NULL in (expected, actual):
        return False
    left, right = _number(expected), _number(actual)
    if left is None or right is None:
        return False
    if _INTEGER.fullmatch(expected) and _INTEGER.fullmatch(actual):
        return left == right
    return abs(left - right) <= TOLERANCE


def _sort_key(row: Sequence[str]) -> tuple[tuple[int, str], ...]:
    """Numbers sort by their value rounded to the tolerance, so 12.5 and 12.50 line up."""
    key = []
    for cell in row:
        number = _number(cell) if cell != NULL else None
        if number is None:
            key.append((1, cell))
        else:
            rounded = number.quantize(TOLERANCE, rounding=ROUND_HALF_EVEN)
            key.append((0, f"{rounded:+040.2f}"))
    return tuple(key)


def compare(expected: Sequence[Row], actual: Sequence[Row], ordered: bool) -> str | None:
    """None when the rows match, else a message naming the first differing row."""
    if len(expected) != len(actual):
        return f"{len(actual)} rows, expected {len(expected)}"
    if not ordered:
        expected, actual = sorted(expected, key=_sort_key), sorted(actual, key=_sort_key)
    for number, (want, got) in enumerate(zip(expected, actual, strict=True), start=1):
        if len(want) != len(got) or not all(
            cells_equal(a, b) for a, b in zip(want, got, strict=True)
        ):
            where = f"row {number}" if ordered else f"row {number} after sorting"
            return f"{where}: expected {want}, got {got}"
    return None


def read_csv(path: Path) -> tuple[Row, list[Row]]:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    if not rows:
        raise ValueError(f"{path} is empty; it needs at least a header row")
    return rows[0], rows[1:]


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="\n")


def sorted_rows(rows: Iterable[Row]) -> list[Row]:
    return sorted(rows, key=_sort_key)
