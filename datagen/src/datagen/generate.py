"""Deterministic generator for sales.orders (see docs/adr/0002-orders-data-model.md).

Every value derives from splitmix64(seed, day, file_index, row_index, column_id), computed
vectorised in numpy. Nothing reads the clock, unseeded randomness or thread order, so the same
profile gives the same bytes on every machine.

`python -m datagen.generate golden` writes the unit profile's batch hashes (tests/golden/unit.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from numpy.typing import NDArray

DEFAULT_SEED = 20261118
ANCHOR = date(2026, 11, 18)
TIMEZONE = "Asia/Kolkata"
UTC_OFFSET_SECONDS = 19_800  # Asia/Kolkata is UTC+05:30 all year (no daylight saving).
EPOCH = date(1970, 1, 1)
DAY_MICROS = 86_400_000_000
ORDER_ID_DAY_STRIDE = 1_000_000_000
TENANTS = 400
CUSTOMERS = 1_000_000
MIN_PAISE = 100
PAISE_RANGE = 2_500_000

# Weights in basis points (sum 10,000).
REGIONS = (
    ("Bengaluru", 2500),
    ("Mumbai", 2200),
    ("Delhi NCR", 2000),
    ("Hyderabad", 1300),
    ("Pune", 1100),
    ("Chennai", 500),
    ("Kolkata", 200),
    ("Ahmedabad", 200),
)
STATUSES = (("paid", 9100), ("pending", 500), ("refunded", 400))

# Column ids double as Iceberg field ids (datagen.write.ORDERS_SCHEMA uses the same numbers).
COL_TENANT, COL_CUSTOMER, COL_REGION, COL_STATUS, COL_AMOUNT, COL_CREATED_AT = 2, 3, 4, 5, 6, 7

ARROW_SCHEMA = pa.schema(
    [
        pa.field("order_id", pa.int64(), nullable=False),
        pa.field("tenant_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("amount", pa.decimal128(14, 2), nullable=False),
        pa.field("created_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

U64 = NDArray[np.uint64]
MASK = (1 << 64) - 1
GAMMA, MUL1, MUL2 = 0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB


@dataclass(frozen=True)
class Profile:
    name: str
    days: int
    files_per_day: int
    rows_per_file: int

    @property
    def rows_per_day(self) -> int:
        return self.files_per_day * self.rows_per_file

    @property
    def total_rows(self) -> int:
        return self.days * self.rows_per_day


PROFILES = {
    p.name: p
    for p in (
        Profile("unit", days=2, files_per_day=2, rows_per_file=100),
        Profile("ci", days=30, files_per_day=4, rows_per_file=10_000),
        Profile("small", days=150, files_per_day=8, rows_per_file=20_000),
        Profile("bench", days=150, files_per_day=8, rows_per_file=1_500_000),
    )
}


def profile_dates(profile: Profile, anchor: date = ANCHOR) -> list[date]:
    """The profile's Kolkata calendar days, oldest first, ending on the anchor."""
    return [anchor - timedelta(days=profile.days - 1 - i) for i in range(profile.days)]


def epoch_day(day: date) -> int:
    return (day - EPOCH).days


def mix(x: int) -> int:
    """splitmix64 on one Python int (wrapping to 64 bits)."""
    z = (x + GAMMA) & MASK
    z = ((z ^ (z >> 30)) * MUL1) & MASK
    z = ((z ^ (z >> 27)) * MUL2) & MASK
    return z ^ (z >> 31)


def mix_array(x: U64) -> U64:
    """splitmix64 on a uint64 array; numpy uint64 arithmetic wraps like the scalar version."""
    z = x + np.uint64(GAMMA)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(MUL1)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(MUL2)
    result: U64 = z ^ (z >> np.uint64(31))
    return result


def row_hashes(seed: int, day: date, file_index: int, rows: int) -> U64:
    """Per-row state: mix chained over seed, day and file, then each row index."""
    prefix = mix(mix(mix(seed) ^ epoch_day(day)) ^ file_index)
    return mix_array(np.uint64(prefix) ^ np.arange(rows, dtype=np.uint64))


def column_hash(rows: U64, column_id: int) -> U64:
    return mix_array(rows ^ np.uint64(column_id))


def weighted_choice(hashes: U64, weights: tuple[tuple[str, int], ...]) -> pa.Array:
    thresholds = np.cumsum([w for _, w in weights])
    if thresholds[-1] != 10_000:
        raise ValueError(f"weights must sum to 10000 basis points, not {thresholds[-1]}")
    picks = np.searchsorted(thresholds, hashes % np.uint64(10_000), side="right")
    names = pa.array([name for name, _ in weights], pa.string())
    return names.take(pa.array(picks.astype(np.int64)))


def prefixed_ids(prefix: str, numbers: NDArray[np.int64], width: int) -> pa.Array:
    digits = pc.utf8_lpad(pc.cast(pa.array(numbers), pa.string()), width=width, padding="0")
    return pc.binary_join_element_wise(prefix, digits, "")


def decimal_from_paise(paise: NDArray[np.int64]) -> pa.Array:
    """Exact decimal(14,2) from non-negative integer paise: unscaled int128 = paise."""
    if paise.size and int(paise.min()) < 0:
        raise ValueError("paise must be non-negative")
    words = np.zeros((paise.size, 2), dtype="<u8")
    words[:, 0] = paise
    return pa.Array.from_buffers(
        pa.decimal128(14, 2), paise.size, [None, pa.py_buffer(words.tobytes())]
    )


def generate_batch(
    seed: int, day: date, file_index: int, rows: int, first_position: int
) -> pa.RecordBatch:
    """One file's worth of rows. `first_position` is the day position of the first row."""
    state = row_hashes(seed, day, file_index, rows)
    positions = np.arange(first_position, first_position + rows, dtype=np.int64)
    order_id = epoch_day(day) * ORDER_ID_DAY_STRIDE + positions + 1
    tenant = (column_hash(state, COL_TENANT) % np.uint64(TENANTS)).astype(np.int64) + 1
    customer = (column_hash(state, COL_CUSTOMER) % np.uint64(CUSTOMERS)).astype(np.int64) + 1
    paise = (column_hash(state, COL_AMOUNT) % np.uint64(PAISE_RANGE)).astype(np.int64) + MIN_PAISE
    day_start = (epoch_day(day) * 86_400 - UTC_OFFSET_SECONDS) * 1_000_000
    offset = (column_hash(state, COL_CREATED_AT) % np.uint64(DAY_MICROS)).astype(np.int64)
    return pa.RecordBatch.from_arrays(
        [
            pa.array(order_id, pa.int64()),
            prefixed_ids("t-", tenant, 4),
            prefixed_ids("c-", customer, 7),
            weighted_choice(column_hash(state, COL_REGION), REGIONS),
            weighted_choice(column_hash(state, COL_STATUS), STATUSES),
            decimal_from_paise(paise),
            pa.array(day_start + offset, pa.timestamp("us", tz="UTC")),
        ],
        schema=ARROW_SCHEMA,
    )


def day_batches(profile: Profile, seed: int, day: date) -> Iterator[pa.RecordBatch]:
    """The base rows of one day: files_per_day batches of rows_per_file rows."""
    for file_index in range(profile.files_per_day):
        yield generate_batch(
            seed, day, file_index, profile.rows_per_file, file_index * profile.rows_per_file
        )


def batch_digest(batch: pa.RecordBatch) -> str:
    """sha256 of a batch's values in a canonical text form (independent of Arrow layout)."""
    digest = hashlib.sha256()
    for name, column in zip(batch.schema.names, batch.columns, strict=True):
        if pa.types.is_timestamp(column.type):
            column = column.cast(pa.int64())
        values = "\n".join(str(v) for v in column.to_pylist())
        digest.update(f"{name}\n{values}\n".encode())
    return digest.hexdigest()


def golden(profile: Profile, seed: int) -> dict[str, Any]:
    return {
        "command": "make golden (python -m datagen.generate golden --out tests/golden/unit.json)",
        "profile": profile.name,
        "seed": seed,
        "anchor_date": ANCHOR.isoformat(),
        "tool_versions": tool_versions(),
        "batches": {
            f"{day.isoformat()}/file-{i}": batch_digest(batch)
            for day in profile_dates(profile)
            for i, batch in enumerate(day_batches(profile, seed, day))
        },
    }


def tool_versions() -> dict[str, str]:
    return {"python": platform.python_version(), "numpy": np.__version__, "pyarrow": pa.__version__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m datagen.generate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gold = sub.add_parser("golden", help="write the unit profile's batch hashes")
    gold.add_argument("--out", type=Path, required=True)
    gold.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    result = golden(PROFILES["unit"], args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(result['batches'])} batch hashes to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
