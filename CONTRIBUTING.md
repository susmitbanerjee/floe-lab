# Contributing

## Prerequisites

- GNU Make 4+, Git, and Bash (Git Bash on Windows).
- [uv](https://docs.astral.sh/uv/) at the version in `pyproject.toml` (`required-version`).
  uv installs the Python pinned in `.python-version`.
- Docker with Compose v2. shellcheck, hadolint, terraform, tflint and gitleaks run in
  containers pinned by digest, so you do not install them.
- On Windows, Smart App Control (Windows Security, App & browser control) can block the
  native modules of mypy, pyarrow, pyiceberg-core, pyroaring and duckdb with "An Application
  Control policy has blocked this file". Turn it off, or run the targets from WSL or a Linux
  container.

## Make targets

Make is the only entry point. `make help` lists every target:

| Target  | What it does |
| ------- | ------------ |
| `setup` | Create `.venv` from `uv.lock` |
| `lint`  | ruff format and check, mypy, shellcheck, hadolint, `docker compose config`, terraform fmt and tflint (when `aws/` has `.tf` files), gitleaks |
| `test`  | pytest, skipping tests marked `stack` |
| `fmt`   | ruff format and safe fixes |
| `up`    | Start RustFS, Postgres and Lakekeeper on 127.0.0.1, bootstrap the bucket and the `lab` warehouse, run the smoke test. Creates `.env` from `.env.example` if missing |
| `down`  | Stop the stack, keep its volumes |
| `reset` | Stop the stack and delete its volumes |
| `logs`  | Follow the stack's logs |
| `smoke` | Rerun the S3 and catalog smoke test |
| `test-stack` | Stack tests; they run `make reset`, so they delete the lab's data |
| `data`  | Write `sales.orders` for `PROFILE=unit\|ci\|small\|bench` and `data/manifest-<profile>.json`. Refuses if the table exists; `FORCE=1` rebuilds. `CATALOG=glue` targets AWS Glue |
| `append` | `DAYS=n` adds days after the last one; `ROWS=n` adds rows to the last day. One snapshot each |
| `data-tpch` | TPC-H at `SF=0.01` (default) or `SF=1` into namespace `tpch_sf0_01` / `tpch_sf1` |
| `golden` | Regenerate `tests/golden/unit.json`; only when ADR 0002's data model changes |
| `check` | Expected answers for `PROFILE` in `checks/expected/<profile>/`: DuckDB over the Parquet files pyiceberg lists, plus `files.json` and `PROVENANCE.json` |
| `check-tpch` | The 22 TPC-H queries and answers for `SF=0.01` or `SF=1` from DuckDB's tpch extension |
| `verify` | Recompute `PROFILE`'s and the TPC-H answers and fail on any difference |
| `check-floe` | Run `PROFILE`'s queries through a running Floe (`FLOE_DSN`, default `postgresql://127.0.0.1:5432/lake`) |
| `export` | Copy the TPC-H and orders answers into `FLOE=../floe`'s `tests/expected/` and nothing else |

Before you open a pull request, run `make lint test` and paste the output in the description.

Install the git hooks once with `uv run --locked pre-commit install`.

## Pull requests

- Keep pull requests small and use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `build:`, `ci:`, `test:`, `chore:`).
- Record significant decisions in `docs/adr/` using `0000-template.md`.

## Developer Certificate of Origin

Every commit must be signed off under the [Developer Certificate of Origin](https://developercertificate.org/).
Sign off with `git commit -s`, which adds:

```text
Signed-off-by: Your Name <you@example.com>
```

By signing off you certify that you wrote the change or otherwise have the right to submit it
under the Apache-2.0 license.
