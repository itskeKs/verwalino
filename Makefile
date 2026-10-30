# Verwalino developer workflow. `make help` lists the targets.
#
# Backend tests need the disposable test database (AGENTS.md): the container
# `infra-postgres-1` on 127.0.0.1:55432, started with
#   docker compose -f infra/docker-compose.declaration-test.yml up -d postgres
# The connection settings come from scripts/test-env.sh (sourced below). Never
# point these targets at the production database; the test harness refuses it.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Backend runs through uv. `test` needs dev (pytest), ops (psycopg2 for the
# Alembic fixtures) and ocr (pikepdf/pytesseract used by the PDF preflight tests).
UV_TEST := uv run --locked --extra dev --extra ops --extra ocr
UV_LINT := uv run --locked --extra dev

# Extra pytest arguments, e.g. make test-backend PYTEST_ARGS="-q tests/test_x.py -k name"
PYTEST_ARGS ?= -q

.PHONY: help test test-agent-rules test-backend test-installer test-frontend test-bdd typecheck lint lint-backend lint-frontend \
        docs-check frontend-deps hooks check-testdb

help: ## List the documented targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---- tests -----------------------------------------------------------------

test: test-agent-rules test-installer test-backend test-frontend typecheck ## Whole suite before a merge: agent rules + installer contracts + backend pytest (incl. BDD) + frontend vitest + typecheck

test-agent-rules: ## Paired agent rules and Codex hook contracts (stdlib only; no DB)
	python3 -B scripts/test_agent_rules.py

test-backend: check-testdb ## Backend pytest against the test DB (PYTEST_ARGS="-q tests/test_x.py -k name" narrows it)
	@( . ./scripts/test-env.sh && cd backend && $(UV_TEST) pytest $(PYTEST_ARGS) )

test-installer: ## Self-host installer contract tests (no DB; Docker is faked)
	cd backend && $(UV_LINT) python ../scripts/test-self-host.py

test-frontend: frontend/node_modules ## Frontend vitest (single run)
	cd frontend && npm test

test-bdd: ## BDD scenarios only (backend/tests/bdd); skipped while the directory does not exist
	@if [ -d backend/tests/bdd ]; then \
	  $(MAKE) --no-print-directory check-testdb && \
	  ( . ./scripts/test-env.sh && cd backend && $(UV_TEST) pytest tests/bdd $(PYTEST_ARGS) ); \
	else \
	  echo "test-bdd: backend/tests/bdd does not exist yet - skipped"; \
	fi

typecheck: frontend/node_modules ## Frontend TypeScript check (tsc -b, no emit)
	cd frontend && npx --no-install tsc -b

# ---- static checks ---------------------------------------------------------

lint: lint-backend lint-frontend typecheck ## ruff + eslint + tsc: the static gate CI runs (the pre-commit hook lints only what is staged)

lint-backend: ## ruff check over backend/ (rule set: [tool.ruff] in backend/pyproject.toml)
	cd backend && $(UV_LINT) ruff check .

lint-frontend: frontend/node_modules ## eslint over frontend/
	cd frontend && npm run lint

docs-check: ## Relative Markdown links and image paths resolve (docs/archive/ excluded)
	python3 scripts/check_docs.py

# ---- setup -----------------------------------------------------------------

frontend-deps: frontend/node_modules ## Install frontend dependencies (npm ci) when missing or outdated

frontend/node_modules: frontend/package-lock.json
	cd frontend && npm ci --no-audit --no-fund
	@touch frontend/node_modules

hooks: ## Enable the git hooks in .githooks (pre-commit -> scripts/checks.sh)
	git config core.hooksPath .githooks
	@echo "Enabled .githooks: 'git commit' now runs ruff/eslint on staged code and the docs check on staged Markdown (scripts/checks.sh)."
	@echo "Add tests with VERWALINO_HOOK_TESTS=fast|full; skip lint with VERWALINO_HOOK_LINT=0, docs with VERWALINO_HOOK_DOCS=0; bypass all with VERWALINO_HOOK_SKIP=1 or git commit --no-verify."

check-testdb:
	@(exec 3<>/dev/tcp/127.0.0.1/55432) 2>/dev/null || { \
	  echo "No database on 127.0.0.1:55432. Start the test DB (only this compose file, see AGENTS.md):" >&2; \
	  echo "  docker compose -f infra/docker-compose.declaration-test.yml up -d postgres" >&2; \
	  exit 1; }
