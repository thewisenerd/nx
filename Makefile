default: format lint

format:
	uv run ruff format src/
	uv run ruff check --extend-select I --fix

lint:
	uv run mypy --strict src/nx

.PHONY: default format lint
