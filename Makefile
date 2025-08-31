default: format lint

format:
	uv run ruff format src/

lint:
	uv run mypy --strict src/nx

.PHONY: default format lint
