default: format lint

format:
    uv run ruff format src/
    uv run ruff check --extend-select I --fix

lint:
    uv run ty check

install-shim:
    uv tool install -p 3.13 --editable .

bump:
    jj new
    uv version --bump patch
    jj commit -m "v$(uv version --short)"
    jj tag set -r @- "v$(uv version --short)"

build:
    uv build
    [ -n "$NX_PUBLISH_HOST" ] && scp "dist/nx-$(uv version --short).tar.gz" "$NX_PUBLISH_HOST:static/nx/" || true
