default: format lint

format:
	uv run ruff format src/
	uv run ruff check --extend-select I --fix

lint:
	uv run ty check

install-shim:
	uv tool install -p 3.13 --editable .

bump:
	test -z "$$(git status --porcelain)" || (echo "uncommitted changes" && exit 1)
	uv version --bump patch
	git add pyproject.toml uv.lock
	git commit -m "v$$(uv version --short)"
	git tag "v$$(uv version --short)"

.PHONY: default format lint install-shim bump
