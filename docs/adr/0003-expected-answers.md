# 0003: Expected answers, file counts and the export to Floe

- Status: accepted
- Date: 2026-11-28

## Context

Floe's tests and the spike report need answers they can trust, and the number of data files a
correct scan should touch. An answer is only trustworthy if it comes from a different engine
reading the same files, and if it does not move with the calendar.

Two things in the existing repos did not match the plan:

1. No design doc in either repo holds the 7-day region query. Floe's tests use
   `SELECT region, count(*), sum(amount) FROM sales.orders GROUP BY region ORDER BY region`,
   and `docs/floe-lab.md` in Floe expects a `region,count,sum` header.
2. Floe's `tests/tpch.rs` reads `tests/expected/tpch-sf0.01/qNN.csv` as `|`-separated, with
   `qNN.sql` beside each answer. The plan asks for comma CSV.

## Options

- Region query: (a) Floe's query plus the 7-day filter, or (b) wait for the deck's text.
- TPC-H format in Floe: (a) comma CSV everywhere, and Floe's parser adapts; or (b) comma CSV in
  floe-lab, with the export converting to Floe's current `|` format.

## Decision

Chosen by the project owner on 2026-11-28: region (a) and TPC-H (a).

- **Queries** live in `checks/queries/*.sql` as plain Postgres-compatible SQL. They use fixed
  `TIMESTAMPTZ '... +05:30'` literals, never `now()`, and alias every output column so DuckDB
  and Postgres give the same header. `region_7d.sql` is Floe's region query with
  `created_at >= TIMESTAMPTZ '2026-11-12 00:00:00+05:30'`.
- **Answers** come from `checks/compute.py`. pyiceberg lists the current snapshot's data files
  (refusing if any delete files exist). DuckDB then reads exactly those Parquet files through
  httpfs and runs each query. pyiceberg and DuckDB are both independent of Floe.
- **CSV format:** comma-separated with a header row and standard CSV quoting. An empty field
  is NULL. Decimals keep their scale (`12.50`). Rows of a query without `ORDER BY` are written
  sorted, so files are stable. The comparator ignores order for them.
- **File counts:** `files.json` counts, for `region_7d` and `late_day`, the data files whose
  `day(created_at)` partition overlaps the query's time range. This is what a scan that prunes
  by partition only must read. Column statistics may let an engine read fewer files, but never
  more, and the answer must not change.
- **TPC-H** queries and answers come from DuckDB's `tpch` extension (`tpch_queries()`,
  `tpch_answers()`), converted from its `|` text to CSV. The final newline ends the last row,
  so q17's `avg_yearly\n\n` is one row holding NULL.
- **Provenance:** every expected directory has a `PROVENANCE.json` with the profile or scale
  factor, seed, snapshot ids, tool versions and the exact command. Snapshot ids and table UUIDs
  change on every rebuild. `make verify` reports those changes but fails only on differences
  in answers, file counts or row and file totals.
- **Export:** `make export FLOE=../floe` writes inside Floe's `tests/expected/` only:
  `tpch-sf0.01/` and `tpch-sf1/` (`qNN.sql`, `qNN.csv`, `LICENSE-duckdb`, `PROVENANCE.json`),
  `orders-<profile>/` (`region_7d.csv`, `files.json`, `PROVENANCE.json`) and `README.md`. It
  overwrites those files and leaves every other file, such as `KNOWN_FAILURES.md`, alone.

## Consequences

- Floe's `tests/tpch.rs` must parse comma CSV with quoting (TPC-H strings contain commas)
  before it uses an exported `tpch-sf0.01/`. Until then, its TPC-H suite fails against the
  export.
- Floe's `docs/floe-lab.md` still describes an older contract: MinIO, warehouse `demo`,
  `checks/region.csv` and `region_files_scanned.txt`. Floe's side needs updating to read
  `tests/expected/orders-<profile>/region_7d.csv` and `files.json`. floe-lab does not edit Floe.
- Committed answers hold for a table built by `make data` alone. After `make append`,
  `make verify` reports the changed answers, and that is how it should behave.
