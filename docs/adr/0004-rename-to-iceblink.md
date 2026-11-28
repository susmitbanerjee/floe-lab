# 0004: Rename Floe to Iceblink

- Status: accepted
- Date: 2026-11-28

## Context

The engine this lab tests was called Floe, and this repository floe-lab. The project is now
Iceblink, and the GitHub repository is `susmitbanerjee/IceBlink-lab`. This repository never
used the interim name RocketLake, and no earlier rename ADR exists to supersede.

`git grep -n -I -i -E 'floe|rocketlake'` on `main` at 8f32334 found 141 lines in 35 files: the
package and compose project name, `FLOE_*` environment variables, the bucket, lab credentials,
Iceberg table and snapshot properties (`floe-lab.*`), the smoke-test payload seed, one make
target and its variable, two file names, and prose naming the engine. The pull request lists
them by group.

## Options

1. Rename the brand everywhere mechanically, engine-facing names included.
2. Rename the brand, and make names that mean "the engine under test" brand-free, so a later
   rename does not touch them.

## Decision

Option 2. Apply this map and nothing else:

| Old | New |
|---|---|
| Floe, RocketLake (prose) | Iceblink |
| floe, rocketlake (lower-case names) | iceblink |
| floe-lab | iceblink-lab |
| floe.lab.toml / floe.glue.toml.example | iceblink.lab.toml / iceblink.glue.toml.example |
| `FLOE_*`, `ROCKETLAKE_*` environment variables | `ICEBLINK_*` |
| bucket floe-lab | iceblink-lab |
| lab credentials floe-lab-access / floe-lab-secret | iceblink-lab-access / iceblink-lab-secret |
| IAM roles floe-lab-reader / floe-lab-writer | iceblink-lab-reader / iceblink-lab-writer |
| tag project=floe-lab, Athena workgroup floe-lab | project=iceblink-lab, iceblink-lab |

Names that mean "the engine" become brand-free. Environment variables keep the brand.

| Old | New |
|---|---|
| `checks/src/checks/run_floe.py`, `tests/test_run_floe.py` | `run_engine.py`, `test_run_engine.py` |
| make target `check-floe` | `check-engine` |
| make variable `FLOE=../floe`, `--floe` flag of `checks.export` | `ENGINE=../iceblink`, `--engine` |
| env `FLOE_DSN` | `ICEBLINK_DSN` |
| docs/floe-workflow.md, `--floe-bin`, `FLOE_BIN`, `floe_version`, `floe-config`, `floe-env` | docs/engine-workflow.md, `--engine-bin`, `ICEBLINK_BIN`, `engine_version`, `engine-config`, `engine-env` |

Nothing else changes: the warehouse `lab`, the database `lake`, table names, profiles, seeds,
the anchor date, every expected answer, `results/` and ADR numbers stay as they are.

Not in this repository yet, so nothing to rename: contract.json, Terraform (`aws/` has no
`.tf` files), the toml configs, the bench runner and its results, IAM roles, the Athena
workgroup, `make ci-lab`, `engine-config`/`engine-env` and a pull request template. Later
changes create them under the new names.

The lab's own names change with the map: Iceberg table properties and snapshot summary keys
become `iceblink-lab.profile`, `iceblink-lab.seed`, `iceblink-lab.anchor-date`,
`iceblink-lab.day` and `iceblink-lab.kind`. The smoke test's payload seed becomes
`iceblink-lab-smoke-v1`, which changes its test hash, but no expected answer changes.

`scripts/check-legacy-name.sh` runs in `make lint` (and so in CI). It fails if
`git grep -n -I -i -w -E 'floe|rocketlake'` finds anything outside the allow-list:

- rename ADRs (`docs/adr/*-rename-to-*.md`), `CHANGELOG.md` and `results/`;
- `docs/troubleshooting.md`, because it must name the old volumes to remove;
- the script itself, because it holds the pattern.

## Consequences

- The compose project is now `iceblink-lab`. The old project's containers and its volumes
  (`floe-lab_rustfs-data`, `floe-lab_postgres-data`) are orphaned. Stop them, remove the
  volumes, delete `.env` (it holds `FLOE_*` names), then `make up` and regenerate the data.
  `docs/troubleshooting.md` gives the commands.
- Tables written before the rename carry `floe-lab.*` properties, which the new code does not
  read. Rebuild them with `make data PROFILE=<name> FORCE=1`.
- Committed expected answers keep every CSV. Each `PROVENANCE.json` is regenerated, so its
  package name, snapshot ids and table UUID change; ADR 0003 explains why ids always change.
- Iceblink's repository must rename its own side: the lab doc it keeps for this repository,
  and anything that reads `FLOE_LAB_*` or `checks/region.csv`.
