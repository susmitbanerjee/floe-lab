# Changelog

## Unreleased

### Renamed: Floe is now Iceblink (ADR 0004)

- The repository is iceblink-lab (was floe-lab), and the Python project is `iceblink-lab`.
- The compose project, the bucket, the lab credentials (`iceblink-lab-access` /
  `iceblink-lab-secret`) and the Postgres and encryption values use iceblink-lab.
- `FLOE_LAB_*` and `FLOE_DSN` environment variables are now `ICEBLINK_LAB_*` and
  `ICEBLINK_DSN`.
- Iceberg table properties and snapshot summary keys are now `iceblink-lab.*` (were
  `floe-lab.*`). Rebuild old tables with `make data PROFILE=<name> FORCE=1`.
- Names that mean the engine are brand-free: `make check-engine` (was `check-floe`),
  `make export ENGINE=../iceblink` (was `FLOE=../floe`, flag `--floe`), and
  `checks/src/checks/run_engine.py` (was `run_floe.py`).
- No expected answer changed. `PROVENANCE.json` files were regenerated (package name,
  snapshot ids, table UUIDs).
- Upgrading a local lab: see `docs/troubleshooting.md`, "Old volumes after the rename".
- There is no contract.json yet, so there is no contract version to bump.
