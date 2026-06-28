# claude-test-kit — developer task runner.
# Toolchain (ruff + mypy + pre-commit + the `ci` aggregate) is borrowed from
# awesome-claude-code. `run_tests.sh` remains the zero-setup, venv-bootstrapping
# entry point; these targets assume a dev env (`make install`).

PYTHON ?= python3

.PHONY: help install test test-unit test-integration cov lint format format-check mypy caps ci clean

help:
	@echo "Targets:"
	@echo "  make install          - pip install -e \".[dev]\" (ruff, mypy, pytest, pre-commit)"
	@echo "  make test             - run the full pytest suite"
	@echo "  make test-unit        - fast unit tests only (-m unit)"
	@echo "  make test-integration - integration tests only (-m integration)"
	@echo "  make cov              - run tests with coverage"
	@echo "  make lint             - ruff check"
	@echo "  make format           - ruff format (writes changes)"
	@echo "  make format-check     - ruff format --check (CI gate)"
	@echo "  make mypy             - type-check ctk + caps"
	@echo "  make caps             - python -m caps status"
	@echo "  make ci               - format-check + lint + mypy + test + caps gate"
	@echo "  make clean            - remove caches and build artifacts"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest -m unit

test-integration:
	$(PYTHON) -m pytest -m integration

cov:
	$(PYTHON) -m pytest --cov=ctk --cov=caps --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

mypy:
	$(PYTHON) -m mypy

caps:
	$(PYTHON) -m caps status

# Mirrors awesome-claude-code's `ci` target: every quality gate in one command.
# The caps gate enforces that declared capabilities are still proven & fresh.
ci: format-check lint mypy test
	$(PYTHON) -m caps status --check

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
