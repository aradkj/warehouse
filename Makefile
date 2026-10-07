# Developer entry points. Everything here is a thin wrapper around the real
# command, so nothing about the build is hidden in the Makefile.

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF   ?= .venv/bin/ruff
MYPY   ?= .venv/bin/mypy
ALEMBIC ?= .venv/bin/alembic

DATABASE_URL ?= postgresql+asyncpg://arad:arad@localhost:5432/arad
TEST_DATABASE_URL ?= postgresql+asyncpg://arad:arad@localhost:5432/arad_test

.PHONY: help install lint format typecheck check test test-unit test-application \
        test-repository test-api test-integration test-concurrency migrate \
        migrate-test downgrade run compose-up compose-down measure clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /: /' | sort

install:  ## Create the virtualenv and install the project with dev extras
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

lint:  ## Lint every source tree
	$(RUFF) check src/ tests/ migrations/ scripts/

format:  ## Format every source tree
	$(RUFF) format src/ tests/ migrations/ scripts/
	$(RUFF) check --fix src/ tests/ migrations/ scripts/

typecheck:  ## Static types over the application source
	$(MYPY) src/

check: lint typecheck  ## Lint and typecheck

test:  ## Run the whole suite
	$(PYTEST) -q

test-unit:          ## Domain, value objects, architecture rules
	$(PYTEST) tests/unit -q
test-application:   ## Use cases against in-memory fakes
	$(PYTEST) tests/application -q
test-repository:    ## Repositories against PostgreSQL
	$(PYTEST) tests/repository -q
test-api:           ## HTTP contract through the ASGI app
	$(PYTEST) tests/api -q
test-integration:   ## Migrations, drift detection, schema constraints
	$(PYTEST) tests/integration -q
test-concurrency:   ## Parallel transactions racing for stock
	$(PYTEST) tests/concurrency -q

migrate:  ## Apply migrations to the application database
	ARAD_DATABASE_URL=$(DATABASE_URL) $(ALEMBIC) upgrade head

migrate-test:  ## Apply migrations to the test database
	ARAD_DATABASE_URL=$(TEST_DATABASE_URL) $(ALEMBIC) upgrade head

downgrade:  ## Roll the application database back one revision
	ARAD_DATABASE_URL=$(DATABASE_URL) $(ALEMBIC) downgrade -1

run:  ## Serve the API locally
	ARAD_DATABASE_URL=$(DATABASE_URL) $(PYTHON) -m bootstrap.cli serve

compose-up:  ## Start the Docker environment
	docker compose up --build

compose-down:  ## Stop the Docker environment and drop its volume
	docker compose down -v

measure:  ## Measure the hot queries with EXPLAIN ANALYZE
	$(PYTHON) scripts/measure_indexes.py --rows 50000

clean:  ## Remove caches and bytecode
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
