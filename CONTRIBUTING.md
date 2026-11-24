# Contributing

## Prerequisites

- GNU Make 4+, Git, and Bash (Git Bash on Windows).
- [uv](https://docs.astral.sh/uv/) at the version in `pyproject.toml` (`required-version`).
  uv installs the Python pinned in `.python-version`.
- Docker with Compose v2. shellcheck, hadolint, terraform, tflint and gitleaks run in
  containers pinned by digest, so you do not install them.

## Make targets

Make is the only entry point. `make help` lists every target:

| Target  | What it does |
| ------- | ------------ |
| `setup` | Create `.venv` from `uv.lock` |
| `lint`  | ruff format and check, mypy, shellcheck, hadolint, `docker compose config`, terraform fmt and tflint (when `aws/` has `.tf` files), gitleaks |
| `test`  | pytest, skipping tests marked `stack` |
| `fmt`   | ruff format and safe fixes |

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
