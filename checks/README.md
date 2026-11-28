# checks

Queries (`queries/*.sql`, `queries/tpch/`), their expected answers (`expected/<profile>/`,
`expected/tpch-sf<SF>/`) and the scripts in `src/checks/`: `compute.py` (DuckDB over pyiceberg's
file list), `run_floe.py` (`make check-floe`), `export.py` (`make export`). See ADR 0003.
