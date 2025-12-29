default: format lint

format:
	uv run ruff format src/
	uv run ruff check --extend-select I --fix

lint:
	uv run ty check

install-shim:
	uv tool install -p 3.13 --editable .

.PHONY: default format lint install-shim
