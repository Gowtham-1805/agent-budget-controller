# Agent Budget Controller
#
# `make help` lists everything.

SHELL := /bin/sh
VENV  := .venv
PY    := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
endif
PYTEST := $(PY) -m pytest
TF     := terraform -chdir=infrastructure/terraform

.DEFAULT_GOAL := help
.PHONY: help setup test test-unit test-contract test-concurrency test-property \
        test-failure test-e2e test-acceptance test-all test-ci lint format typecheck check \
        run up down logs build tf-init tf-validate tf-plan deploy destroy \
        smoke demo verify-pricing clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup: ## Create the virtualenv and install dependencies
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,providers,observability]"
	@echo "Copy .env.example to .env and fill in your credentials."

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test: ## Run the full suite
	$(PYTEST) tests/ -q

test-unit: ## Domain, money, windows, pricing, adapters
	$(PYTEST) tests/unit -q

test-contract: ## Same assertions against both backends (in-memory and DynamoDB via moto)
	$(PYTEST) tests/contract -q

test-concurrency: ## The hard-cap race and three-agent contention
	$(PYTEST) tests/concurrency -q -p no:xdist

test-property: ## Hypothesis: the accounting identity under random operation orders
	$(PYTEST) tests/property -q

test-failure: ## Failure injection: ambiguous outcomes, crashes, idempotency
	$(PYTEST) tests/failure_injection -q

test-e2e: ## End-to-end through the HTTP API
	$(PYTEST) tests/e2e -q

test-acceptance: ## The mandated challenge scenarios
	$(PYTEST) tests/acceptance -q

test-all: ## Everything, verbosely
	$(PYTEST) tests/ -v

test-ci: ## CI run: deeper property search, fails on the first invariant break
	$(PYTEST) tests/ -q --hypothesis-profile=thorough

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

lint: ## Lint
	$(PY) -m ruff check apps tests

format: ## Auto-format
	$(PY) -m ruff format apps tests
	$(PY) -m ruff check --fix apps tests

typecheck: ## Type-check the gateway
	$(PY) -m mypy apps/gateway/src/abc_gateway

check: lint typecheck test ## Lint, type-check, and test

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

run: ## Run the gateway locally against the in-memory store
	ABC_USE_MEMORY_STORE=true ABC_ENVIRONMENT=local \
	$(PY) -m uvicorn abc_gateway.main:create_app --factory --reload \
		--app-dir apps/gateway/src --port 8080

up: ## Start the local stack (gateway + DynamoDB Local)
	docker compose up --build -d
	@echo "Gateway on http://localhost:8080 -- try: curl http://localhost:8080/healthz"

down: ## Stop the local stack
	docker compose down -v

logs: ## Tail the local gateway logs
	docker compose logs -f gateway

build: ## Build the container image
	docker build -t agent-budget-controller:local .

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

tf-init: ## Initialise Terraform
	$(TF) init

tf-validate: ## Validate the Terraform configuration
	$(TF) fmt -check
	$(TF) validate

tf-plan: ## Plan the deployment (requires AWS credentials)
	$(TF) plan

deploy: ## Apply the deployment (requires AWS credentials; incurs real cost)
	@echo "This creates billable AWS resources. Run 'make destroy' when finished."
	$(TF) apply

destroy: ## Tear down all AWS resources
	@echo "The DynamoDB tables have prevent_destroy set; remove it deliberately"
	@echo "if you really intend to delete live budget state and the ledger."
	$(TF) destroy

# ---------------------------------------------------------------------------
# Validation against a deployment
# ---------------------------------------------------------------------------

smoke: ## Run the deployed smoke test (set GATEWAY_URL and ABC_ADMIN_API_KEY)
	$(PY) scripts/smoke_test.py

demo: ## Run the full demo scenario against a running gateway
	$(PY) scripts/demo.py

verify-pricing: ## Check real-provider catalog rates for staleness (no API calls)
	$(PY) scripts/verify_pricing.py

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
