.PHONY: help install install-dev lint format test typecheck run-cli run-ui api serve clean

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
	uv run streamlit run src/genesis/ui/asgi_app.py --server.address $${GENESIS_UI_HOST:-0.0.0.0} --server.port $${GENESIS_UI_PORT:-3000} --server.headless true

run-ui: ui  ## Deprecated alias for `make ui`

api:  ## Start the FastAPI server for the default user (GENESIS_API_PORT, default 8765)
	uv run genesis-api

# Run UI and API together. Uses Make's parallel-job mechanism
# (``-j 2``) so GNU Make itself handles signal propagation: when
# Ctrl+C arrives at the parent make, it forwards SIGTERM/SIGINT to
# every child process it spawned and waits for them to exit. This
# is the same pattern genesis-scaffolding/Makefile uses for its
# ``dev`` target and avoids any custom trap logic in the recipe.
serve:  ## Start UI and API together (Ctrl+C to stop both)
	@echo "Starting UI on $${GENESIS_UI_PORT:-3000} and API on $${GENESIS_API_PORT:-8765} (Ctrl+C to stop both)..."
	@$(MAKE) -j 2 ui api

# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------

clean:  ## Remove cached files and venv
	uv clean
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
