"""Write sales.orders through an Iceberg catalog and describe it in data/manifest-<profile>.json.

    python -m datagen.write data --profile ci [--catalog lab|glue] [--force]
    python -m datagen.write append --days 1 | --rows 214380 [--catalog lab|glue]

Each Kolkata day is one pyiceberg append, so one snapshot. Data is generated and written day by
day, so memory holds one day at a time. The manifest is rebuilt from the table's own snapshot
summaries after every write, so its numbers are what the catalog recorded.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pyarrow as pa
import requests
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.table.snapshots import Snapshot
from pyiceberg.transforms import DayTransform
from pyiceberg.types import (
    DecimalType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from datagen.catalog import CATALOGS, open_catalog
from datagen.generate import (
    ANCHOR,
    ARROW_SCHEMA,
    DEFAULT_SEED,
    PROFILES,
    TIMEZONE,
    Profile,
    day_batches,
    generate_batch,
    profile_dates,
)
from datagen.settings import Settings, SettingsError

NAMESPACE = "sales"
ORDERS = (NAMESPACE, "orders")
ORDERS_SCHEMA = Schema(
    NestedField(1, "order_id", LongType(), required=True),
    NestedField(2, "tenant_id", StringType(), required=True),
    NestedField(3, "customer_id", StringType(), required=True),
    NestedField(4, "region", StringType(), required=True),
    NestedField(5, "status", StringType(), required=True),
    NestedField(6, "amount", DecimalType(14, 2), required=True),
    NestedField(7, "created_at", TimestamptzType(), required=True),
)
ORDERS_SPEC = PartitionSpec(
    PartitionField(source_id=7, field_id=1000, transform=DayTransform(), name="created_at_day")
)
PROP_PROFILE, PROP_SEED, PROP_ANCHOR = "floe-lab.profile", "floe-lab.seed", "floe-lab.anchor-date"
SNAP_DAY, SNAP_KIND = "floe-lab.day", "floe-lab.kind"
KIND_DAY, KIND_ROWS = "day", "rows"
DATA_DIR = Path("data")


class RefusedError(RuntimeError):
    """The table already holds data and the caller did not ask to replace it."""


def target_file_size(profile: Profile, seed: int) -> int:
    """Arrow bytes per file (pyiceberg bins by in-memory size) for rows_per_file rows."""
    sample = generate_batch(seed, ANCHOR, 0, min(profile.rows_per_file, 10_000), 0)
    return max(1, int(round(sample.nbytes / sample.num_rows * profile.rows_per_file)))


def create_orders(catalog: Catalog, profile: Profile, seed: int, force: bool) -> Table:
    catalog.create_namespace_if_not_exists(NAMESPACE)
    if catalog.table_exists(ORDERS):
        held = catalog.load_table(ORDERS).properties.get(PROP_PROFILE, "data not written by make")
        if not force:
            raise RefusedError(
                f"sales.orders already holds profile {held}; run make reset or make data FORCE=1"
            )
        print(f"FORCE=1: dropping sales.orders (profile {held}) and its files")
        catalog.purge_table(ORDERS)
    return catalog.create_table(
        ORDERS,
        schema=ORDERS_SCHEMA,
        partition_spec=ORDERS_SPEC,
        properties={
            "format-version": "2",
            "write.target-file-size-bytes": str(target_file_size(profile, seed)),
            PROP_PROFILE: profile.name,
            PROP_SEED: str(seed),
            PROP_ANCHOR: ANCHOR.isoformat(),
        },
    )


def append_snapshot(
    table: Table, batches: Iterable[pa.RecordBatch], day: date, kind: str
) -> Snapshot:
    """One append, so one snapshot, tagged with the day and the kind of write."""
    data = pa.Table.from_batches(list(batches), schema=ARROW_SCHEMA)
    table.append(data, snapshot_properties={SNAP_DAY: day.isoformat(), SNAP_KIND: kind})
    snapshot = table.current_snapshot()
    if snapshot is None:
        raise RuntimeError(f"append for {day} left the table without a current snapshot")
    summary: Mapping[str, str] = snapshot.summary or {}
    print(
        f"{day}  {kind:<4}  {int(summary.get('added-records') or 0):>10,} rows  "
        f"{int(summary.get('added-data-files') or 0):>3} files  "
        f"{int(summary.get('added-files-size') or 0) / 1e6:>9.1f} MB  "
        f"snapshot {snapshot.snapshot_id}"
    )
    return snapshot


def write_profile(catalog: Catalog, profile: Profile, seed: int, force: bool) -> Table:
    table = create_orders(catalog, profile, seed, force)
    for day in profile_dates(profile):
        append_snapshot(table, day_batches(profile, seed, day), day, KIND_DAY)
    return table


def load_orders(catalog: Catalog) -> tuple[Table, Profile, int]:
    if not catalog.table_exists(ORDERS):
        raise RefusedError("sales.orders does not exist yet; run make data PROFILE=<name> first")
    table = catalog.load_table(ORDERS)
    name = table.properties.get(PROP_PROFILE)
    if name not in PROFILES:
        raise RefusedError(f"sales.orders has no known {PROP_PROFILE} property; run make data")
    return table, PROFILES[name], int(table.properties[PROP_SEED])


def append_days(catalog: Catalog, days: int) -> Table:
    table, profile, seed = load_orders(catalog)
    last = max(day_summaries(table))
    for offset in range(1, days + 1):
        day = date.fromisoformat(last) + timedelta(days=offset)
        append_snapshot(table, day_batches(profile, seed, day), day, KIND_DAY)
    return table


def append_rows(catalog: Catalog, rows: int) -> Table:
    """Append rows to the last day. They hash as new files and continue the day's order ids."""
    table, profile, seed = load_orders(catalog)
    days = day_summaries(table)
    last = max(days)
    earlier_row_appends = sum(1 for s in days[last]["snapshots"] if s["kind"] == KIND_ROWS)
    file_index = profile.files_per_day + earlier_row_appends
    batch = generate_batch(
        seed, date.fromisoformat(last), file_index, rows, first_position=days[last]["rows"]
    )
    append_snapshot(table, [batch], date.fromisoformat(last), KIND_ROWS)
    return table


def day_summaries(table: Table) -> dict[str, dict[str, Any]]:
    """Per-day totals from the snapshot summaries of every tagged snapshot, oldest first."""
    days: dict[str, dict[str, Any]] = {}
    for snapshot in sorted(table.snapshots(), key=lambda s: s.sequence_number or 0):
        summary: Mapping[str, str] = snapshot.summary or {}
        day = summary.get(SNAP_DAY)
        if day is None:
            continue
        entry = days.setdefault(
            day, {"date": day, "rows": 0, "data_files": 0, "bytes": 0, "snapshots": []}
        )
        added = {
            "rows": int(summary.get("added-records") or 0),
            "data_files": int(summary.get("added-data-files") or 0),
            "bytes": int(summary.get("added-files-size") or 0),
        }
        for key, value in added.items():
            entry[key] += value
        kind = summary.get(SNAP_KIND)
        entry["snapshots"].append({"snapshot_id": snapshot.snapshot_id, "kind": kind, **added})
    return dict(sorted(days.items()))


def build_manifest(
    table: Table, catalog_kind: str, command: str, versions: dict[str, str]
) -> dict[str, Any]:
    current = table.current_snapshot()
    summary: Mapping[str, str] = (current.summary if current else None) or {}
    days = list(day_summaries(table).values())
    manifest = {
        "profile": table.properties[PROP_PROFILE],
        "seed": int(table.properties[PROP_SEED]),
        "anchor_date": table.properties[PROP_ANCHOR],
        "timezone": TIMEZONE,
        "catalog": catalog_kind,
        "table": ".".join(ORDERS),
        "table_uuid": str(table.metadata.table_uuid),
        "format_version": table.metadata.format_version,
        "partition_spec": "day(created_at)",
        "target_file_size_bytes": int(table.properties["write.target-file-size-bytes"]),
        "command": command,
        "current_snapshot_id": current.snapshot_id if current else None,
        "snapshot_count": sum(len(d["snapshots"]) for d in days),
        "day_count": len(days),
        "total_rows": int(summary.get("total-records") or 0),
        "total_data_files": int(summary.get("total-data-files") or 0),
        "total_bytes": int(summary.get("total-files-size") or 0),
        "days": days,
        "tool_versions": versions,
    }
    if manifest["total_rows"] != sum(d["rows"] for d in days):
        raise RuntimeError("table total-records differs from the sum of daily appends")
    return manifest


def tool_versions(settings: Settings, catalog_kind: str) -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        **{name: version(name) for name in ("floe-lab", "numpy", "pyarrow", "pyiceberg")},
    }
    if catalog_kind == "lab":
        info = requests.get(f"{settings.management_url}/info", timeout=30)
        info.raise_for_status()
        versions["lakekeeper"] = str(info.json().get("lakekeeper-version"))
    return versions


def save_manifest(manifest: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"manifest-{manifest['profile']}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def print_summary(manifest: dict[str, Any], path: Path) -> None:
    days = manifest["days"]
    print(
        f"\n{manifest['table']} profile {manifest['profile']} on catalog {manifest['catalog']}: "
        f"{manifest['day_count']} days ({days[0]['date']} .. {days[-1]['date']}, "
        f"{manifest['timezone']}), {manifest['snapshot_count']} snapshots\n"
        f"  rows {manifest['total_rows']:,}  data files {manifest['total_data_files']:,}  "
        f"bytes {manifest['total_bytes']:,} ({manifest['total_bytes'] / 1e9:.2f} GB)\n"
        f"  manifest {path}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m datagen.write", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    data = sub.add_parser("data", help="write a profile from scratch")
    data.add_argument("--profile", choices=sorted(PROFILES), required=True)
    data.add_argument("--seed", type=int, default=DEFAULT_SEED)
    data.add_argument("--force", action="store_true", help="drop and rebuild an existing table")
    append = sub.add_parser("append", help="append to the existing table")
    what = append.add_mutually_exclusive_group(required=True)
    what.add_argument("--days", type=int, help="days after the last day, one snapshot each")
    what.add_argument("--rows", type=int, help="rows added to the last day as one snapshot")
    for p in (data, append):
        p.add_argument("--catalog", choices=CATALOGS, default="lab")
    args = parser.parse_args(argv)
    for name in ("days", "rows"):
        value = getattr(args, name, None)
        if value is not None and value < 1:
            parser.error(f"--{name} must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    try:
        settings = Settings.from_env()
        catalog = open_catalog(settings, args.catalog)
        if args.command == "data":
            table = write_profile(catalog, PROFILES[args.profile], args.seed, args.force)
        elif args.days is not None:
            table = append_days(catalog, args.days)
        else:
            table = append_rows(catalog, args.rows)
        command = "python -m datagen.write " + " ".join(argv)
        manifest = build_manifest(
            table, args.catalog, command, tool_versions(settings, args.catalog)
        )
    except (SettingsError, RefusedError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    print_summary(manifest, save_manifest(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
