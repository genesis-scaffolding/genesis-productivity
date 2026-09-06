.PHONY: help install install-dev lint format test typecheck run-cli run-ui clean

# -----------------------------------------------------------------------------
# Meta
# -----------------------------------------------------------------------------

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sed 's/##/  /' | \
		while read -r cmd desc; do \
			printf "%-15s %s\n" "$$cmd" "$$desc"; \
		done

# -----------------------------------------------------------------------------
# Install
# -----------------------------------------------------------------------------

install:  ## Install dependencies
	uv sync

install-dev:  ## Install dependencies including dev tools
	uv sync

# -----------------------------------------------------------------------------
# Code quality
# -----------------------------------------------------------------------------

lint:  ## Auto-fix and check lint issues
	uv run ruff check src/ tests/ --fix

format:  ## Format code with ruff
	uv run ruff format src/ tests/

typecheck:  ## Run pyright type checker
	uv run pyright src/ tests/

# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------

test:  ## Run unit tests (fast, no Radicale)
	uv run pytest -v -m "not integration"

test-all:  ## Run all tests including integration
	uv run pytest -v

test-integration:  ## Run integration tests only
	uv run pytest -v -m integration

# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------

run-cli:  ## Run the CLI app
	uv run genesis

ui:  ## Start the Streamlit UI for the default user
	uv run streamlit run src/genesis/ui/app.py --server.address 127.0.0.1 --server.port 3000 --server.headless true

run-ui: ui  ## Deprecated alias for `make ui`

# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------

clean:  ## Remove cached files and venv
	uv clean
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
