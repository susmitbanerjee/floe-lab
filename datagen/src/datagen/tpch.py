"""Write the 8 TPC-H tables at a scale factor into namespace tpch_sf<SF>, unpartitioned.

    python -m datagen.tpch --sf 0.01 [--catalog lab|glue] [--force]

DuckDB's tpch extension generates the data (CALL dbgen(sf => ...)), which is deterministic for a
given scale factor and DuckDB version. Each table streams into Iceberg as one append, and the
run writes data/manifest-tpch_sf<SF>.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path
from typing import Any

from pyiceberg.catalog import Catalog

from datagen.catalog import CATALOGS, open_catalog
from datagen.settings import Settings, SettingsError

TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")
BATCH_ROWS = 100_000
DATA_DIR = Path("data")


class RefusedError(RuntimeError):
    """The namespace already holds tables and the caller did not ask to replace them."""


def parse_scale_factor(text: str) -> Decimal:
    try:
        sf = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"scale factor {text!r} is not a number") from None
    if not sf.is_finite() or sf <= 0:
        raise argparse.ArgumentTypeError(f"scale factor must be positive, not {text}")
    return sf


def namespace_for(sf: Decimal) -> str:
    """0.01 -> tpch_sf0_01, 1 -> tpch_sf1, 1.0 -> tpch_sf1."""
    text = format(sf.normalize(), "f")
    return "tpch_sf" + text.replace(".", "_")


def prepare_namespace(catalog: Catalog, namespace: str, force: bool) -> None:
    catalog.create_namespace_if_not_exists(namespace)
    existing = [ident for ident in catalog.list_tables(namespace)]
    if not existing:
        return
    if not force:
        raise RefusedError(
            f"{namespace} already holds {len(existing)} tables; run make reset or "
            "make data-tpch FORCE=1"
        )
    print(f"FORCE=1: dropping {len(existing)} tables in {namespace} and their files")
    for ident in existing:
        catalog.purge_table(ident)


def write_tables(catalog: Catalog, sf: Decimal, namespace: str) -> dict[str, dict[str, Any]]:
    import duckdb  # imported here so the CLI and its tests load without DuckDB's native module

    con = duckdb.connect()
    con.execute("INSTALL tpch")
    con.execute("LOAD tpch")
    # sf is a validated positive Decimal, so formatting it into SQL is safe.
    con.execute(f"CALL dbgen(sf => {format(sf, 'f')})")
    written: dict[str, dict[str, Any]] = {}
    for name in TABLES:
        reader = con.table(name).to_arrow_reader(BATCH_ROWS)
        if reader is None:
            raise RuntimeError(f"DuckDB returned no reader for {name}")
        table = catalog.create_table(
            (namespace, name), schema=reader.schema, properties={"format-version": "2"}
        )
        table.append(reader)
        snapshot = table.current_snapshot()
        summary: Mapping[str, str] = (snapshot.summary if snapshot else None) or {}
        written[name] = {
            "snapshot_id": snapshot.snapshot_id if snapshot else None,
            "rows": int(summary.get("total-records") or 0),
            "data_files": int(summary.get("total-data-files") or 0),
            "bytes": int(summary.get("total-files-size") or 0),
        }
        print(
            f"{namespace}.{name:<9} {written[name]['rows']:>10,} rows  "
            f"{written[name]['data_files']:>3} files  {written[name]['bytes'] / 1e6:>8.1f} MB"
        )
    return written


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="python -m datagen.tpch", description=__doc__)
    parser.add_argument("--sf", type=parse_scale_factor, required=True)
    parser.add_argument("--catalog", choices=CATALOGS, default="lab")
    parser.add_argument("--force", action="store_true", help="drop and rebuild existing tables")
    args = parser.parse_args(argv)
    namespace = namespace_for(args.sf)
    try:
        catalog = open_catalog(Settings.from_env(), args.catalog)
        prepare_namespace(catalog, namespace, args.force)
    except (SettingsError, RefusedError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    tables = write_tables(catalog, args.sf, namespace)
    manifest = {
        "dataset": "tpch",
        "scale_factor": format(args.sf.normalize(), "f"),
        "catalog": args.catalog,
        "namespace": namespace,
        "command": "python -m datagen.tpch " + " ".join(argv),
        "total_rows": sum(t["rows"] for t in tables.values()),
        "total_bytes": sum(t["bytes"] for t in tables.values()),
        "tables": tables,
        "tool_versions": {
            "python": platform.python_version(),
            **{n: version(n) for n in ("floe-lab", "duckdb", "pyarrow", "pyiceberg")},
        },
    }
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"manifest-{namespace}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\n{namespace}: {len(tables)} tables, {manifest['total_rows']:,} rows, manifest {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
