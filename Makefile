default: format lint

format:
	uv run ruff format src/
	uv run ruff check --extend-select I --fix

lint:
	uv run ty check

.PHONY: default format lint
