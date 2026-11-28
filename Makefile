# floe-lab: every task is a make target. `make help` lists them.
SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules

# Stop Git Bash on Windows from rewriting container paths such as /src.
export MSYS_NO_PATHCONV := 1

UV_RUN := uv run --locked

# Lint tools run in containers pinned by digest. The readable tag is in the comment above each.
# koalaman/shellcheck:v0.11.0
SHELLCHECK_IMAGE := koalaman/shellcheck@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d
# hadolint/hadolint:v2.15.1
HADOLINT_IMAGE := hadolint/hadolint@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d
# hashicorp/terraform:1.17.2
TERRAFORM_IMAGE := hashicorp/terraform@sha256:6e9c1e647c31a960dc9b59e13272f9111d90010c10b717f77ffb324767660475
# ghcr.io/terraform-linters/tflint:v0.66.0
TFLINT_IMAGE := ghcr.io/terraform-linters/tflint@sha256:65b9944a9274c4989492c1d79998c2801664bf8a55fddb4bd3a9e6d73a006ddc
# ghcr.io/gitleaks/gitleaks:v8.30.1
GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

# Read-only mount of the repo, no network.
DOCKER_RUN := docker run --rm --network none -v "$(CURDIR):/src:ro" -w /src

# Compose for targets that only stop or inspect the stack: .env when present, else the example.
COMPOSE := docker compose --env-file $(if $(wildcard .env),.env,.env.example)

# Files git tracks or would track (respects .gitignore, so .venv and data/ are skipped).
repo_files = $(shell git ls-files --cached --others --exclude-standard -- $(1))

# Data knobs: make data PROFILE=ci, make append DAYS=1 or ROWS=214380, make data-tpch SF=1.
PROFILE ?= small
CATALOG ?= lab
SF ?= 0.01
FORCE_FLAG = $(if $(filter 1,$(FORCE)),--force)

.PHONY: help setup lint test fmt up down reset logs smoke test-stack data append data-tpch golden

help: ## List every target with its one-line description
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-z-]*:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .venv from uv.lock (Python, dev tools, floe-lab packages)
	uv sync --locked

lint: ## Run every linter and secret scan; fails on the first problem
	$(UV_RUN) ruff format --check .
	$(UV_RUN) ruff check .
	$(UV_RUN) python -m mypy
	$(call lint_files,shellcheck,'*.sh',$(SHELLCHECK_IMAGE))
	$(call lint_files,hadolint,'*Dockerfile',$(HADOLINT_IMAGE) hadolint)
	docker compose --env-file .env.example config -q
	@if compgen -G 'aws/*.tf' > /dev/null; then \
	  $(DOCKER_RUN) -w /src/aws $(TERRAFORM_IMAGE) fmt -check -recursive -diff; \
	  $(DOCKER_RUN) -w /src/aws $(TFLINT_IMAGE) --recursive; \
	else echo "lint: aws/ has no .tf files yet, skipping terraform fmt and tflint"; fi
	$(DOCKER_RUN) $(GITLEAKS_IMAGE) detect --source . --redact --no-banner

test: ## Run unit tests (everything not marked stack)
	$(UV_RUN) python -m pytest -m "not stack"

fmt: ## Format code and apply safe lint fixes
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

# Lab settings: created from the example the first time any target needs them.
.env:
	cp .env.example .env
	@echo "created .env from .env.example (fake lab values)"

up: .env ## Start the stack, bootstrap the bucket and warehouse, then run the smoke test
	docker compose up --wait --wait-timeout 120
	$(UV_RUN) --env-file .env python -m datagen.bootstrap
	$(UV_RUN) --env-file .env python -m datagen.smoke

down: ## Stop the stack and keep its data volumes
	$(COMPOSE) down

reset: ## Stop the stack and delete its data volumes
	$(COMPOSE) down --volumes --remove-orphans

logs: ## Follow the stack's logs
	$(COMPOSE) logs --follow --tail 200

smoke: .env ## Rerun the S3 and catalog smoke test against the running stack
	$(UV_RUN) --env-file .env python -m datagen.smoke

test-stack: .env ## Run the stack tests (they reset the stack and delete its data volumes)
	$(UV_RUN) --env-file .env python -m pytest -m stack

data: .env ## Write sales.orders for PROFILE=unit|ci|small|bench (CATALOG=lab|glue, FORCE=1)
	$(UV_RUN) --env-file .env python -m datagen.write data --profile $(PROFILE) --catalog $(CATALOG) $(FORCE_FLAG)

append: .env ## Append DAYS=n days after the last one, or ROWS=n rows to the last day
	$(UV_RUN) --env-file .env python -m datagen.write append --catalog $(CATALOG) $(if $(DAYS),--days $(DAYS)) $(if $(ROWS),--rows $(ROWS))

data-tpch: .env ## Write the 8 TPC-H tables at SF=0.01 (or SF=1) into namespace tpch_sf<SF>
	$(UV_RUN) --env-file .env python -m datagen.tpch --sf $(SF) --catalog $(CATALOG) $(FORCE_FLAG)

golden: ## Regenerate tests/golden/unit.json (only when ADR 0002's data model changes)
	$(UV_RUN) python -m datagen.generate golden --out tests/golden/unit.json

# $(call lint_files,name,git pathspecs,image and command): run the tool on matching files, or skip.
define lint_files
@files="$(call repo_files,$(2))"; \
if [ -n "$$files" ]; then echo "$(1): $$files"; $(DOCKER_RUN) $(3) $$files; \
else echo "lint: no files for $(1), skipping"; fi
endef
