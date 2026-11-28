"""Run the checks' queries through the running engine (Iceblink) and compare with expectations.

    python -m checks.run_engine --profile ci [--dsn postgresql://127.0.0.1:5432/lake]

Prints one line per query (ok, fail with the first differing row, or error with SQLSTATE) and
exits 1 on any failure. For region_7d it also runs EXPLAIN ANALYZE and compares the engine's
files_scanned with files.json: equal is a partition-only scan, lower is fine because the answer
already matched, and higher fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from checks.compare import compare, has_order_by, read_csv, render
from checks.compute import EXPECTED_DIR, orders_queries

DEFAULT_DSN = "postgresql://127.0.0.1:5432/lake"
FILES_SCANNED = re.compile(r"files_scanned=(\d+)")
EXPLAINED = ("region_7d",)


class QueryError(Exception):
    """A query failed on the server; carries its SQLSTATE."""

    def __init__(self, sqlstate: str | None, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate or "?????"


class Database(Protocol):
    """What the runner needs from a connection: rows for a SQL string (lets tests fake it)."""

    def rows(self, sql: str) -> list[tuple[Any, ...]]: ...


@dataclass(frozen=True)
class Outcome:
    query: str
    status: str  # ok | fail | error
    detail: str = ""

    def line(self) -> str:
        return f"{self.status:<5} {self.query}" + (f": {self.detail}" if self.detail else "")


class PsycopgDatabase:
    def __init__(self, dsn: str) -> None:
        import psycopg  # imported here: Windows Smart App Control may block libpq

        self._psycopg = psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                return list(cur.fetchall()) if cur.description else []
        except self._psycopg.Error as exc:
            raise QueryError(exc.sqlstate, str(exc).strip()) from exc


def files_scanned(plan: Sequence[tuple[Any, ...]]) -> int | None:
    """The first `files_scanned=N` in the plan text, as Iceblink's own tests/lab.rs reads it."""
    text = "\n".join(" ".join(render(cell) for cell in row) for row in plan)
    match = FILES_SCANNED.search(text)
    return int(match.group(1)) if match else None


def check_query(db: Database, sql_path: Path, expected_dir: Path) -> list[Outcome]:
    name = sql_path.stem
    sql = sql_path.read_text(encoding="utf-8")
    _, expected = read_csv(expected_dir / f"{name}.csv")
    try:
        actual = [[render(v) for v in row] for row in db.rows(sql)]
    except QueryError as exc:
        return [Outcome(name, "error", f"SQLSTATE {exc.sqlstate}: {exc}")]
    difference = compare(expected, actual, ordered=has_order_by(sql))
    outcomes = [Outcome(name, "fail", difference) if difference else Outcome(name, "ok")]
    if name in EXPLAINED:
        outcomes.append(check_files_scanned(db, name, sql, expected_dir))
    return outcomes


def check_files_scanned(db: Database, name: str, sql: str, expected_dir: Path) -> Outcome:
    label = f"{name} files_scanned"
    wanted = json.loads((expected_dir / "files.json").read_text())["queries"][name]["data_files"]
    try:
        plan = db.rows("EXPLAIN ANALYZE " + sql.strip().rstrip(";"))
    except QueryError as exc:
        return Outcome(label, "error", f"SQLSTATE {exc.sqlstate}: {exc}")
    scanned = files_scanned(plan)
    if scanned is None:
        return Outcome(label, "fail", "EXPLAIN ANALYZE output has no files_scanned=N")
    if scanned > wanted:
        return Outcome(label, "fail", f"{scanned}, more than the {wanted} files in range")
    if scanned < wanted:
        return Outcome(label, "ok", f"{scanned} < {wanted} in range (pruned below partitions)")
    return Outcome(label, "ok", f"{scanned} = files in range")


def run(db: Database, profile: str) -> list[Outcome]:
    expected_dir = EXPECTED_DIR / profile
    if not expected_dir.is_dir():
        raise SystemExit(f"no expected answers in {expected_dir}; run make check PROFILE={profile}")
    outcomes = []
    for sql_path in orders_queries():
        outcomes += check_query(db, sql_path, expected_dir)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.run_engine", description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--dsn", default=os.environ.get("ICEBLINK_DSN", DEFAULT_DSN))
    args = parser.parse_args(argv)
    try:
        db = PsycopgDatabase(args.dsn)
    except Exception as exc:  # any connection failure: say what to do and stop
        print(f"cannot connect to the engine at {args.dsn}: {exc}", file=sys.stderr)
        print(
            "Start `iceblink serve`, or set ICEBLINK_DSN=postgresql://host:port/db.",
            file=sys.stderr,
        )
        return 1
    outcomes = run(db, args.profile)
    for outcome in outcomes:
        print(outcome.line())
    bad = sum(o.status != "ok" for o in outcomes)
    print(f"check-engine: {len(outcomes) - bad} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
