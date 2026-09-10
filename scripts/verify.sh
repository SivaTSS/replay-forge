#!/usr/bin/env bash
set -euo pipefail

: "${UV_CACHE_DIR:=/tmp/replayforge-uv-cache}"
: "${PLAYWRIGHT_BROWSERS_PATH:=/tmp/replayforge-playwright-browsers}"
: "${npm_config_cache:=/tmp/replayforge-npm-cache}"
export UV_CACHE_DIR PLAYWRIGHT_BROWSERS_PATH npm_config_cache

uv sync --extra dev --frozen
npx --yes pnpm@10.15.1 install --frozen-lockfile
uv run ruff format --check backend pyproject.toml
uv run ruff check backend
uv run mypy backend/src backend/tests
uv run pytest --ignore=backend/tests/integration --cov=replayforge --cov-report=term-missing -q
npx --yes pnpm@10.15.1 typecheck
npx --yes pnpm@10.15.1 build
uv run pytest backend/tests/integration -q
