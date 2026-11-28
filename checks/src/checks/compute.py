"""Compute expected answers with DuckDB over the Parquet files pyiceberg lists.

    python -m checks.compute orders --profile ci --out checks/expected/ci
    python -m checks.compute tpch --sf 0.01 --out checks/expected/tpch-sf0.01
    python -m checks.compute verify --profile ci   # recompute into a temp dir and diff

DuckDB and pyiceberg are independent of Floe, and the queries use fixed timestamps, so the
answers are only as moving as the data. See docs/adr/0003-expected-answers.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import requests

from checks.compare import has_order_by, read_csv, render, sorted_rows, write_csv
from datagen.catalog import open_catalog
from datagen.settings import Settings, SettingsError
from datagen.write import ORDERS, PROP_ANCHOR, PROP_PROFILE, PROP_SEED

if TYPE_CHECKING:
    import duckdb
    from pyiceberg.table import Table

CHECKS_DIR = Path(__file__).resolve().parents[2]
QUERIES_DIR = CHECKS_DIR / "queries"
TPCH_QUERIES_DIR = QUERIES_DIR / "tpch"
EXPECTED_DIR = CHECKS_DIR / "expected"
DAY_MICROS = 86_400_000_000
TPCH_SCALE_FACTORS = ("0.01", "1")

# Time ranges of the queries whose file counts go in files.json: [lower, upper), upper None
# means open-ended. tests/test_checks.py asserts these literals appear in the SQL.
PARTITION_RANGES: dict[str, tuple[str, str | None]] = {
    "region_7d": ("2026-11-12 00:00:00+05:30", None),
    "late_day": ("2026-11-18 00:00:00+05:30", "2026-11-19 00:00:00+05:30"),
}
# PROVENANCE.json fields that change on every rebuild; verify reports but ignores them.
VOLATILE = ("snapshot_id", "snapshot_ids", "table_uuid", "command", "tool_versions")


class CheckError(RuntimeError):
    """The lab is not in a state the expected answers can be computed from."""


@dataclass(frozen=True)
class DataFile:
    path: str
    partition_day: int
    rows: int
    bytes: int


def orders_queries() -> list[Path]:
    return sorted(QUERIES_DIR.glob("*.sql"))


def micros(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp).timestamp() * 1_000_000)


def overlapping(files: Sequence[DataFile], lower: str, upper: str | None) -> list[DataFile]:
    """Files whose day partition [day, day + 1) overlaps [lower, upper)."""
    low = micros(lower)
    high = micros(upper) if upper is not None else None
    return [
        f
        for f in files
        if (f.partition_day + 1) * DAY_MICROS > low
        and (high is None or f.partition_day * DAY_MICROS < high)
    ]


def current_files(table: Table) -> list[DataFile]:
    snapshot = table.current_snapshot()
    if snapshot is None:
        raise CheckError("sales.orders has no snapshot yet; run make data")
    files = []
    for task in table.scan(snapshot_id=snapshot.snapshot_id).plan_files():
        if task.delete_files:
            raise CheckError(f"{task.file.file_path} has delete files; the checks expect appends")
        files.append(
            DataFile(
                path=task.file.file_path,
                partition_day=int(task.file.partition[0]),
                rows=int(task.file.record_count),
                bytes=int(task.file.file_size_in_bytes),
            )
        )
    return sorted(files, key=lambda f: f.path)


def duckdb_connection(settings: Settings) -> duckdb.DuckDBPyConnection:
    import duckdb  # imported here: Windows Smart App Control may block its native module

    con = duckdb.connect()
    for extension in ("httpfs", "icu"):
        con.execute(f"INSTALL {extension}")
        con.execute(f"LOAD {extension}")
    con.execute("SET TimeZone = 'UTC'")
    endpoint = urlsplit(settings.s3_endpoint)
    con.execute(
        "CREATE SECRET lab (TYPE s3, KEY_ID $key, SECRET $secret, REGION $region, "
        "ENDPOINT $endpoint, URL_STYLE 'path', USE_SSL $ssl)",
        {
            "key": settings.s3_access_key,
            "secret": settings.s3_secret_key,
            "region": settings.s3_region,
            "endpoint": endpoint.netloc,
            "ssl": endpoint.scheme == "https",
        },
    )
    return con


def run_query(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[list[str]]]:
    result = con.execute(sql)
    header = [column[0] for column in result.description or []]
    rows = [[render(value) for value in row] for row in result.fetchall()]
    return header, rows if has_order_by(sql) else sorted_rows(rows)


def compute_orders(settings: Settings, profile: str, out: Path, command: str) -> dict[str, Any]:
    catalog = open_catalog(settings, "lab")
    if not catalog.table_exists(ORDERS):
        raise CheckError("sales.orders does not exist; run make data PROFILE=<name>")
    table = catalog.load_table(ORDERS)
    held = table.properties.get(PROP_PROFILE)
    if held != profile:
        raise CheckError(
            f"sales.orders holds profile {held}, not {profile}; "
            f"run make data PROFILE={profile} FORCE=1"
        )
    files = current_files(table)
    con = duckdb_connection(settings)
    con.execute("CREATE SCHEMA sales")
    # A view cannot hold parameters, so the file list is quoted into the SQL.
    paths = ", ".join("'" + f.path.replace("'", "''") + "'" for f in files)
    con.execute(f"CREATE VIEW sales.orders AS SELECT * FROM read_parquet([{paths}])")  # noqa: S608

    out.mkdir(parents=True, exist_ok=True)
    queries = orders_queries()
    for query in queries:
        header, rows = run_query(con, query.read_text(encoding="utf-8"))
        write_csv(out / f"{query.stem}.csv", header, rows)
        print(f"{query.stem:<12} {len(rows):>7,} rows")

    counts = {}
    for name, (lower, upper) in PARTITION_RANGES.items():
        hits = overlapping(files, lower, upper)
        counts[name] = {
            "created_at_from": lower,
            "created_at_before": upper,
            "partitions": sorted({_day_text(f.partition_day) for f in hits}),
            "data_files": len(hits),
        }
        print(f"{name:<12} {len(hits):>7,} data files in range (partition pruning only)")
    _write_json(out / "files.json", {"total_data_files": len(files), "queries": counts})

    snapshot = table.current_snapshot()
    provenance = {
        "kind": "orders",
        "profile": profile,
        "seed": int(table.properties[PROP_SEED]),
        "anchor_date": table.properties[PROP_ANCHOR],
        "table": ".".join(ORDERS),
        "table_uuid": str(table.metadata.table_uuid),
        "snapshot_id": snapshot.snapshot_id if snapshot else None,
        "snapshot_ids": [s.snapshot_id for s in table.snapshots()],
        "data_files": len(files),
        "total_rows": sum(f.rows for f in files),
        "total_bytes": sum(f.bytes for f in files),
        "queries": _hashes(queries),
        "command": command,
        "tool_versions": _versions(con, settings),
    }
    _write_json(out / "PROVENANCE.json", provenance)
    return provenance


def compute_tpch(sf: str, out: Path, queries_dir: Path, command: str) -> dict[str, Any]:
    import duckdb  # imported here: Windows Smart App Control may block its native module

    con = duckdb.connect()
    con.execute("INSTALL tpch")
    con.execute("LOAD tpch")
    answers = dict(
        con.execute(
            "SELECT query_nr, answer FROM tpch_answers() WHERE scale_factor = $sf", {"sf": sf}
        ).fetchall()
    )
    if len(answers) != 22:
        raise CheckError(f"DuckDB's tpch extension has {len(answers)} answers for sf {sf}, not 22")
    out.mkdir(parents=True, exist_ok=True)
    queries_dir.mkdir(parents=True, exist_ok=True)
    for number, sql in con.execute("SELECT query_nr, query FROM tpch_queries()").fetchall():
        name = f"q{number:02d}"
        (queries_dir / f"{name}.sql").write_text(
            sql.rstrip() + "\n", encoding="utf-8", newline="\n"
        )
        header, rows = parse_tpch_answer(answers[number])
        write_csv(out / f"{name}.csv", header, rows)
    licence = Path(duckdb.__file__).resolve().parents[1].glob("duckdb-*.dist-info/licenses/LICENSE")
    licence_path = next(licence, None)
    if licence_path is None:
        raise CheckError("cannot find DuckDB's LICENSE in its installed package")
    (out / "LICENSE-duckdb").write_text(licence_path.read_text(encoding="utf-8"), newline="\n")
    provenance = {
        "kind": "tpch",
        "scale_factor": sf,
        "source": "DuckDB tpch extension: tpch_queries() and tpch_answers(), | text to CSV",
        "queries": _hashes(sorted(queries_dir.glob("q*.sql"))),
        "command": command,
        "tool_versions": {
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
            "tpch_extension": _extension_version(con, "tpch"),
        },
        "note": "Correctness tests, not TPC-H benchmark results; do not present them as such.",
    }
    _write_json(out / "PROVENANCE.json", provenance)
    print(f"tpch sf {sf}: 22 queries and answers in {out}")
    return provenance


def parse_tpch_answer(text: str) -> tuple[list[str], list[list[str]]]:
    """DuckDB's answer text: `|`-separated lines, header first, every line ending in a newline."""
    if not text.endswith("\n"):
        raise CheckError("TPC-H answer text does not end with a newline")
    lines = text[:-1].split("\n")
    return lines[0].split("|"), [line.split("|") for line in lines[1:]]


def verify(settings: Settings, profile: str) -> int:
    """Recompute orders and TPC-H answers into a temp dir and diff them with the committed ones."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        compute_orders(settings, profile, root / profile, "verify")
        problems += diff_dirs(EXPECTED_DIR / profile, root / profile)
        for sf in TPCH_SCALE_FACTORS:
            name = f"tpch-sf{sf}"
            compute_tpch(sf, root / name, root / "queries", "verify")
            problems += diff_dirs(EXPECTED_DIR / name, root / name)
            problems += diff_dirs(TPCH_QUERIES_DIR, root / "queries", pattern="q*.sql")
    for problem in problems:
        print(f"DIFF  {problem}")
    print(f"verify: {'no differences' if not problems else f'{len(problems)} differences'}")
    return 1 if problems else 0


def diff_dirs(committed: Path, fresh: Path, pattern: str = "*") -> list[str]:
    problems = []
    names = {p.name for p in committed.glob(pattern)} | {p.name for p in fresh.glob(pattern)}
    for name in sorted(names):
        old, new = committed / name, fresh / name
        if not old.exists() or not new.exists():
            problems.append(
                f"{committed.name}/{name}: only in {'fresh' if new.exists() else 'committed'}"
            )
        elif name == "PROVENANCE.json":
            problems += _diff_provenance(committed.name, old, new)
        elif name.endswith(".csv"):
            if read_csv(old) != read_csv(new):
                problems.append(f"{committed.name}/{name}: answer differs")
        elif old.read_bytes() != new.read_bytes():
            problems.append(f"{committed.name}/{name}: content differs")
    return problems


def _diff_provenance(where: str, old: Path, new: Path) -> list[str]:
    before, after = json.loads(old.read_text()), json.loads(new.read_text())
    problems = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        if key in VOLATILE:
            print(f"note  {where}/PROVENANCE.json {key} changed (expected after a rebuild)")
        else:
            problems.append(f"{where}/PROVENANCE.json {key}: {before.get(key)} -> {after.get(key)}")
    return problems


def _day_text(day: int) -> str:
    return datetime.fromtimestamp(day * 86_400, UTC).date().isoformat()


def _hashes(paths: Sequence[Path]) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, default=_json_default) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot write {type(value).__name__} to JSON")


def _extension_version(con: duckdb.DuckDBPyConnection, name: str) -> str:
    row = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = $name",
        {"name": name},
    ).fetchone()
    return str(row[0]) if row else "unknown"


def _versions(con: duckdb.DuckDBPyConnection, settings: Settings) -> dict[str, str]:
    import duckdb

    info = requests.get(f"{settings.management_url}/info", timeout=30)
    info.raise_for_status()
    return {
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "duckdb_httpfs": _extension_version(con, "httpfs"),
        **{name: version(name) for name in ("floe-lab", "pyiceberg", "pyarrow")},
        "lakekeeper": str(info.json().get("lakekeeper-version")),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="python -m checks.compute", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    orders = sub.add_parser("orders", help="expected answers for sales.orders")
    orders.add_argument("--profile", required=True)
    orders.add_argument("--out", type=Path, required=True)
    tpch = sub.add_parser("tpch", help="TPC-H queries and answers from DuckDB's extension")
    tpch.add_argument("--sf", choices=TPCH_SCALE_FACTORS, required=True)
    tpch.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("verify", help="recompute and diff with the committed answers")
    check.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    command = "python -m checks.compute " + " ".join(argv)
    try:
        if args.command == "tpch":
            compute_tpch(args.sf, args.out, TPCH_QUERIES_DIR, command)
            return 0
        settings = Settings.from_env()
        if args.command == "verify":
            return verify(settings, args.profile)
        compute_orders(settings, args.profile, args.out, command)
    except (SettingsError, CheckError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
