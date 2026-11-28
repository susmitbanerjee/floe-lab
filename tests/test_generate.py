"""Unit tests for the deterministic sales.orders generator (no stack, no network)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from datagen.generate import (
    ANCHOR,
    DEFAULT_SEED,
    PROFILES,
    REGIONS,
    STATUSES,
    UTC_OFFSET_SECONDS,
    batch_digest,
    day_batches,
    epoch_day,
    generate_batch,
    golden,
    mix,
    mix_array,
    profile_dates,
)

GOLDEN = Path(__file__).parent / "golden" / "unit.json"


def test_splitmix64_reference_values() -> None:
    # First outputs of the reference splitmix64 generator seeded with 0 (Vigna).
    assert mix(0) == 0xE220A8397B1DCDAF
    assert mix(0x9E3779B97F4A7C15) == 0x6E789E6AA1B965F4


def test_vector_mix_matches_scalar_mix() -> None:
    values = np.array([0, 1, 12345, 2**63, 2**64 - 1], dtype=np.uint64)
    assert [int(v) for v in mix_array(values)] == [mix(int(v)) for v in values]


def test_unit_batches_match_golden_hashes() -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden(PROFILES["unit"], expected["seed"])["batches"] == expected["batches"]


def test_generation_is_repeatable() -> None:
    day = ANCHOR
    first = generate_batch(DEFAULT_SEED, day, 3, 1000, 0)
    second = generate_batch(DEFAULT_SEED, day, 3, 1000, 0)
    assert batch_digest(first) == batch_digest(second)
    assert batch_digest(first) != batch_digest(generate_batch(DEFAULT_SEED + 1, day, 3, 1000, 0))


def test_profiles_end_on_the_anchor() -> None:
    for profile in PROFILES.values():
        days = profile_dates(profile)
        assert len(days) == profile.days
        assert days[-1] == date(2026, 11, 18)
        assert days == sorted(days)
    assert PROFILES["ci"].total_rows == 1_200_000
    assert PROFILES["small"].total_rows == 24_000_000
    assert PROFILES["bench"].total_rows == 1_800_000_000


def unit_table() -> pa.Table:
    profile = PROFILES["unit"]
    return pa.Table.from_batches(
        [b for d in profile_dates(profile) for b in day_batches(profile, DEFAULT_SEED, d)]
    )


def test_order_ids_are_unique_and_ascending() -> None:
    ids = unit_table().column("order_id").to_numpy()
    assert len(set(ids.tolist())) == len(ids)
    assert bool(np.all(np.diff(ids) > 0))


def test_created_at_falls_inside_the_kolkata_day() -> None:
    profile = PROFILES["unit"]
    for day in profile_dates(profile):
        micros = pa.Table.from_batches(list(day_batches(profile, DEFAULT_SEED, day)))
        values = micros.column("created_at").cast(pa.int64())
        start = (epoch_day(day) * 86_400 - UTC_OFFSET_SECONDS) * 1_000_000
        assert pc.min(values).as_py() >= start
        assert pc.max(values).as_py() < start + 86_400_000_000


def test_columns_have_the_documented_shapes() -> None:
    table = unit_table()
    tenants = set(table.column("tenant_id").to_pylist())
    assert all(t.startswith("t-") and 1 <= int(t[2:]) <= 400 and len(t) == 6 for t in tenants)
    customers = table.column("customer_id").to_pylist()
    assert all(c.startswith("c-") and len(c) == 9 for c in customers)
    amounts: list[Decimal] = table.column("amount").to_pylist()
    assert all(Decimal("1.00") <= a <= Decimal("25000.99") for a in amounts)
    assert all(a.as_tuple().exponent == -2 for a in amounts)
    assert set(table.column("region").to_pylist()) <= {name for name, _ in REGIONS}


@pytest.mark.parametrize(("column", "weights"), [("region", REGIONS), ("status", STATUSES)])
def test_ci_shares_sit_within_half_a_point_of_the_weights(
    column: str, weights: tuple[tuple[str, int], ...]
) -> None:
    profile = PROFILES["ci"]
    counts: Counter[str] = Counter()
    for day in profile_dates(profile):
        for batch in day_batches(profile, DEFAULT_SEED, day):
            for item in pc.value_counts(batch.column(column)).to_pylist():
                counts[item["values"]] += item["counts"]
    total = sum(counts.values())
    assert total == profile.total_rows
    for name, basis_points in weights:
        share = 100 * counts[name] / total
        assert abs(share - basis_points / 100) < 0.5, f"{name}: {share:.3f}%"
