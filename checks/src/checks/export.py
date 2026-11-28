"""Copy expected answers into the engine repository's tests/expected/.

    python -m checks.export --engine ../iceblink

Writes only inside <engine>/tests/expected/: tpch-sf<SF>/ (qNN.sql, qNN.csv, LICENSE-duckdb,
PROVENANCE.json), orders-<profile>/ (region_7d.csv, files.json, PROVENANCE.json) and README.md.
It overwrites those files and leaves everything else, such as KNOWN_FAILURES.md, alone.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from checks.compute import EXPECTED_DIR, TPCH_QUERIES_DIR, TPCH_SCALE_FACTORS

ORDERS_FILES = ("region_7d.csv", "files.json", "PROVENANCE.json")
TPCH_FILES = ("LICENSE-duckdb", "PROVENANCE.json")


class ExportError(RuntimeError):
    """The target is not the engine's checkout or the answers are missing."""


def export(
    engine: Path, expected: Path = EXPECTED_DIR, queries: Path = TPCH_QUERIES_DIR
) -> list[Path]:
    if not (engine / "Cargo.toml").is_file() or not (engine / "tests").is_dir():
        raise ExportError(
            f"{engine} does not look like the engine repository (no Cargo.toml, tests/)"
        )
    target = engine / "tests" / "expected"
    written: list[Path] = []

    def copy(source: Path, dest: Path) -> None:
        if not source.is_file():
            raise ExportError(f"{source} is missing; run make check / make check-tpch first")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        written.append(dest)

    tpch: dict[str, dict[str, Any]] = {}
    for sf in TPCH_SCALE_FACTORS:
        name = f"tpch-sf{sf}"
        for n in range(1, 23):
            copy(queries / f"q{n:02d}.sql", target / name / f"q{n:02d}.sql")
            copy(expected / name / f"q{n:02d}.csv", target / name / f"q{n:02d}.csv")
        for file in TPCH_FILES:
            copy(expected / name / file, target / name / file)
        tpch[name] = json.loads((expected / name / "PROVENANCE.json").read_text())

    orders: dict[str, dict[str, Any]] = {}
    for profile_dir in sorted(p for p in expected.iterdir() if (p / "files.json").is_file()):
        name = f"orders-{profile_dir.name}"
        for file in ORDERS_FILES:
            copy(profile_dir / file, target / name / file)
        orders[name] = json.loads((profile_dir / "PROVENANCE.json").read_text())

    readme = target / "README.md"
    readme.write_text(render_readme(tpch, orders), encoding="utf-8", newline="\n")
    written.append(readme)
    return written


def render_readme(tpch: dict[str, dict[str, Any]], orders: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Expected answers",
        "",
        "Written by iceblink-lab's `make export`; do not edit by hand. iceblink-lab computes",
        "every answer with an engine other than Iceblink and records how in each",
        "`PROVENANCE.json` (iceblink-lab `docs/adr/0003-expected-answers.md`).",
        "",
        "**Format:** comma-separated with a header row and standard CSV quoting. An empty field",
        "is NULL. Numbers with a fractional part compare within 0.01; integers and strings compare",
        "exactly. Rows compare in order only when the query has `ORDER BY`.",
        "",
        "## TPC-H",
        "",
        "`qNN.sql` are the 22 queries and `qNN.csv` their answers, from DuckDB's `tpch` extension",
        "(`tpch_queries()` and `tpch_answers()`), converted from `|` text to CSV.",
        "",
        "| Directory | Scale factor | DuckDB | tpch extension | Command |",
        "|---|---|---|---|---|",
    ]
    for name, p in tpch.items():
        v = p["tool_versions"]
        lines.append(
            f"| `{name}/` | {p['scale_factor']} | {v['duckdb']} | {v['tpch_extension']} "
            f"| `{p['command']}` |"
        )
    lines += [
        "",
        "DuckDB is MIT-licensed; its notice is in each directory's `LICENSE-duckdb`. The queries",
        "and answers derive from the TPC-H specification. These are correctness tests, not TPC-H",
        "benchmark results, and may not be presented as such.",
        "",
        "## sales.orders",
        "",
        "`region_7d.csv` answers iceblink-lab's `checks/queries/region_7d.sql`. `files.json` holds",
        "the number of data files whose `day(created_at)` partition overlaps the query's range:",
        "a scan that prunes by partition only must read exactly that many, and one that prunes",
        "further may read fewer but must give the same answer.",
        "",
        "| Directory | Profile | Seed | Rows | Data files | DuckDB | pyiceberg | Command |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, p in orders.items():
        v = p["tool_versions"]
        lines.append(
            f"| `{name}/` | {p['profile']} | {p['seed']} | {p['total_rows']:,} "
            f"| {p['data_files']:,} | {v['duckdb']} | {v['pyiceberg']} | `{p['command']}` |"
        )
    lines += [
        "",
        "## Known failures",
        "",
        "[`KNOWN_FAILURES.md`](KNOWN_FAILURES.md) is Iceblink's own file; `make export` never",
        "changes it.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.export", description=__doc__)
    parser.add_argument("--engine", type=Path, required=True, help="path to the engine checkout")
    args = parser.parse_args(argv)
    try:
        written = export(args.engine.resolve())
    except ExportError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    target = args.engine.resolve() / "tests" / "expected"
    for path in written:
        print(path.relative_to(target).as_posix())
    print(f"export: {len(written)} files written under {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
