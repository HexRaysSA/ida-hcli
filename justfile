format:
    uv run --quiet ruff format --quiet

ruff:
    uv run --quiet ruff check --quiet --fix
    uv run --quiet ruff check --quiet --select I --fix

mypy:
    uvx --quiet mypy --no-error-summary src/ tests/ --exclude tests/data/ --disable-error-code=import-untyped --disable-error-code=import-not-found

lint: format ruff mypy

test:
    uv run --quiet pytest -x -q --no-header
