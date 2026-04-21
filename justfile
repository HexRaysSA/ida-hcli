format:
    uvx --quiet ruff format --quiet

ruff:
    uvx --quiet ruff check --quiet --fix
    uvx --quiet ruff check --quiet --select I --fix

mypy:
    uvx --quiet mypy --no-error-summary src/ tests/ --exclude tests/data/ --disable-error-code=import-untyped --disable-error-code=import-not-found

lint: format ruff mypy

test:
    uv run --quiet pytest -x -q --no-header
