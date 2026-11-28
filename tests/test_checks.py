"""Hermetic tests for checks.compute and the committed expected answers (no stack, no DuckDB)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from checks.compare import compare, has_order_by, read_csv, render
from checks.compute import (
    EXPECTED_DIR,
    PARTITION_RANGES,
    TPCH_QUERIES_DIR,
    DataFile,
    micros,
    orders_queries,
    overlapping,
    parse_tpch_answer,
)
from datagen.generate import DEFAULT_SEED, PROFILES, day_batches, epoch_day, profile_dates

QUERIES = {q.stem: q for q in orders_queries()}
COMMITTED_PROFILES = ("unit", "ci", "small")


def test_the_six_queries_exist_and_use_no_clock() -> None:
    assert set(QUERIES) == {
        "region_7d",
        "count_all",
        "status_mix",
        "tenant_one",
        "point_order",
        "late_day",
    }
    for path in QUERIES.values():
        code = re.sub(r"--[^\n]*", "", path.read_text()).lower()
        assert "now()" not in code
        assert "current_" not in code


@pytest.mark.parametrize("name", sorted(PARTITION_RANGES))
def test_partition_ranges_match_the_sql(name: str) -> None:
    sql = QUERIES[name].read_text()
    lower, upper = PARTITION_RANGES[name]
    assert f"created_at >= TIMESTAMPTZ '{lower}'" in sql
    if upper is not None:
        assert f"created_at < TIMESTAMPTZ '{upper}'" in sql


def day(text: str) -> int:
    return epoch_day(date.fromisoformat(text))


def test_overlapping_counts_partitions_that_touch_the_range() -> None:
    files = [DataFile(f"f{d}", day(d), 1, 1) for d in ("2026-11-10", "2026-11-11", "2026-11-12")]
    # From 2026-11-12 00:00 IST = 2026-11-11 18:30 UTC: the 11-11 UTC partition overlaps.
    assert [f.path for f in overlapping(files, "2026-11-12 00:00:00+05:30", None)] == [
        "f2026-11-11",
        "f2026-11-12",
    ]
    # [11-11 18:30 UTC, 11-12 18:30 UTC) touches 11-11 and 11-12, not 11-10.
    got = overlapping(files, "2026-11-12 00:00:00+05:30", "2026-11-13 00:00:00+05:30")
    assert [f.path for f in got] == ["f2026-11-11", "f2026-11-12"]
    # A range ending exactly at UTC midnight does not touch the next partition.
    assert overlapping(files, "2026-11-10 00:00:00+00:00", "2026-11-11 00:00:00+00:00") == files[:1]


def test_micros_reads_offsets() -> None:
    assert micros("2026-11-12 00:00:00+05:30") == micros("2026-11-11 18:30:00+00:00")


@pytest.mark.parametrize(
    ("text", "header", "rows"),
    [
        ("a|b\n1|x\n2|y\n", ["a", "b"], [["1", "x"], ["2", "y"]]),
        ("avg_yearly\n\n", ["avg_yearly"], [[""]]),  # q17 at sf 0.01: one row holding NULL
        ("n\n", ["n"], []),
    ],
)
def test_parse_tpch_answer(text: str, header: list[str], rows: list[list[str]]) -> None:
    assert parse_tpch_answer(text) == (header, rows)


@pytest.mark.parametrize("profile", COMMITTED_PROFILES)
def test_committed_answers_have_provenance_and_file_counts(profile: str) -> None:
    folder = EXPECTED_DIR / profile
    provenance = json.loads((folder / "PROVENANCE.json").read_text())
    assert provenance["profile"] == profile
    assert provenance["seed"] == DEFAULT_SEED
    assert provenance["total_rows"] == PROFILES[profile].total_rows
    assert {"duckdb", "pyiceberg", "lakekeeper"} <= set(provenance["tool_versions"])
    assert set(provenance["queries"]) == {f"{name}.sql" for name in QUERIES}
    files = json.loads((folder / "files.json").read_text())
    assert set(files["queries"]) == set(PARTITION_RANGES)
    for name in QUERIES:
        header, _ = read_csv(folder / f"{name}.csv")
        assert header


@pytest.mark.parametrize("sf", ["0.01", "1"])
def test_committed_tpch_answers(sf: str) -> None:
    folder = EXPECTED_DIR / f"tpch-sf{sf}"
    for n in range(1, 23):
        header, _ = read_csv(folder / f"q{n:02d}.csv")
        assert header
        assert (TPCH_QUERIES_DIR / f"q{n:02d}.sql").read_text().strip()
    provenance = json.loads((folder / "PROVENANCE.json").read_text())
    assert provenance["scale_factor"] == sf
    assert "duckdb" in provenance["tool_versions"]
    assert (folder / "LICENSE-duckdb").read_text().strip()


def unit_orders() -> pa.Table:
    profile = PROFILES["unit"]
    return pa.Table.from_batches(
        [b for d in profile_dates(profile) for b in day_batches(profile, DEFAULT_SEED, d)]
    )


def between(table: pa.Table, lower: str, upper: str | None = None) -> pa.Table:
    created = table.column("created_at").cast(pa.int64())
    mask = pc.greater_equal(created, micros(lower))
    if upper is not None:
        mask = pc.and_(mask, pc.less(created, micros(upper)))
    return table.filter(mask)


def grouped(table: pa.Table, key: str) -> list[list[str]]:
    result = table.group_by(key).aggregate([("order_id", "count"), ("amount", "sum")])
    return sorted(
        [
            [render(r[key]), render(r["order_id_count"]), render(r["amount_sum"])]
            for r in result.to_pylist()
        ]
    )


def independent_answers() -> dict[str, list[list[str]]]:
    """The unit profile's answers from the generator with pyarrow: a third engine."""
    orders = unit_orders()
    tenant = between(orders, "2026-10-20 00:00:00+05:30", "2026-11-19 00:00:00+05:30")
    tenant = tenant.filter(pc.equal(tenant.column("tenant_id"), "t-0042")).sort_by("order_id")
    point = orders.filter(pc.equal(orders.column("order_id"), 20775000000001))
    columns = ["order_id", "tenant_id", "customer_id", "region", "status", "amount"]
    return {
        "count_all": [[str(orders.num_rows)]],
        "region_7d": grouped(between(orders, "2026-11-12 00:00:00+05:30"), "region"),
        "status_mix": grouped(orders, "status"),
        "late_day": grouped(
            between(orders, "2026-11-18 00:00:00+05:30", "2026-11-19 00:00:00+05:30"), "status"
        ),
        "point_order": [
            [render(v) for v in row.values()] for row in point.select(columns).to_pylist()
        ],
        "tenant_one": [
            [render(v) for v in row.values()]
            for row in tenant.select(
                ["order_id", "customer_id", "region", "status", "amount"]
            ).to_pylist()
        ],
    }


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_unit_answers_match_an_independent_computation(name: str) -> None:
    _, expected = read_csv(EXPECTED_DIR / "unit" / f"{name}.csv")
    ordered = has_order_by(QUERIES[name].read_text())
    assert compare(expected, independent_answers()[name], ordered=ordered) is None


def test_tpch_queries_dir_is_beside_the_orders_queries() -> None:
    assert TPCH_QUERIES_DIR.parent == next(iter(QUERIES.values())).parent
    assert Path(TPCH_QUERIES_DIR).name == "tpch"
