# floe-lab

floe-lab is the test bench for Floe: it brings up a local storage and
catalog stack, generates deterministic data with known answers, checks Floe's query results
against those answers, and runs benchmarks, all from a clean checkout and all through `make`.
It is not a product; anything that does not help test Floe does not belong here. See
[AGENTS.md](AGENTS.md) for the engineering rules, [docs/adr/](docs/adr/) for decisions, and
[CONTRIBUTING.md](CONTRIBUTING.md) to get started.

```console
$ make help
  help       List every target with its one-line description
  setup      Create .venv from uv.lock (Python, dev tools, floe-lab packages)
  lint       Run every linter and secret scan; fails on the first problem
  test       Run unit tests (everything not marked stack)
  fmt        Format code and apply safe lint fixes
  up         Start the stack, bootstrap the bucket and warehouse, then run the smoke test
  down       Stop the stack and keep its data volumes
  reset      Stop the stack and delete its data volumes
  logs       Follow the stack's logs
  smoke      Rerun the S3 and catalog smoke test against the running stack
  test-stack Run the stack tests (they reset the stack and delete its data volumes)
  data       Write sales.orders for PROFILE=unit|ci|small|bench (CATALOG=lab|glue, FORCE=1)
  append     Append DAYS=n days after the last one, or ROWS=n rows to the last day
  data-tpch  Write the 8 TPC-H tables at SF=0.01 (or SF=1) into namespace tpch_sf<SF>
  golden     Regenerate tests/golden/unit.json (only when ADR 0002's data model changes)
  check      Write checks/expected/PROFILE/ answers with DuckDB over pyiceberg's file list
  check-tpch Write the 22 TPC-H queries and answers for SF=0.01 or SF=1 from DuckDB
  verify     Recompute PROFILE's and TPC-H answers and fail on any difference
  check-floe Run PROFILE's queries through Floe (FLOE_DSN) and report pass or fail per query
  export     Copy TPC-H and orders answers into FLOE=../floe tests/expected/ (nothing else)
```
