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

test:  ## Run tests
	uv run pytest -v

# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------

run-cli:  ## Run the CLI app
	uv run myproject

run-ui:  ## Start the Streamlit UI
	uv run streamlit run src/myproject/ui/app.py

# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------

clean:  ## Remove cached files and venv
	uv clean
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
