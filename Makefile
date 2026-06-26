.PHONY: install setup init-db dev lint format test eval-deterministic smoke release-check clean

install:
	uv sync --locked --all-groups

init-db:
	uv run aeroops-init-db --reset --db-path data/aeroops.db

setup: install init-db

dev:
	uv run streamlit run src/aeroops/app.py

lint:
	uv run ruff format --check src tests scripts
	uv run ruff check src tests scripts

format:
	uv run ruff format src tests scripts

test:
	uv run pytest tests/ -v -ra

eval-deterministic:
	uv run pytest tests/test_evaluation_cases.py -v

smoke:
	uv run python scripts/smoke_test_mcp.py
	uv run python scripts/smoke_test.py
	uv run python scripts/streamlit_process_smoke_test.py

release-check: install lint eval-deterministic smoke
	uv run python scripts/secret_scanner.py
	uv run python scripts/validate_workflows.py
	uv run python scripts/validate_public_docs.py
	uv run python scripts/verify_db_artifact.py
	uv run pytest tests/ -v -ra
	uv run pytest tests/ -W error::ResourceWarning

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .artifacts/
