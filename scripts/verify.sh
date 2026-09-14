#!/usr/bin/env bash
set -euo pipefail

: "${UV_CACHE_DIR:=/tmp/replayforge-uv-cache}"
: "${PLAYWRIGHT_BROWSERS_PATH:=/tmp/replayforge-playwright-browsers}"
: "${npm_config_cache:=/tmp/replayforge-npm-cache}"
export UV_CACHE_DIR PLAYWRIGHT_BROWSERS_PATH npm_config_cache

uv sync --extra dev --frozen
uv run python scripts/check_docs.py
uv run python -m unittest discover -s scripts -p 'test_check_docs.py'
uv run playwright install chromium
npx --yes pnpm@10.15.1 install --frozen-lockfile
uv run ruff format --check backend scripts pyproject.toml
uv run ruff check backend scripts
uv run mypy \
  backend/src \
  backend/tests \
  scripts/check_docs.py \
  scripts/test_check_docs.py \
  scripts/verify_evidence.py \
  scripts/export_evidence.py \
  scripts/verify_evidence_bundles.py \
  scripts/capture_demo_workflows.py
uv run python scripts/verify_evidence_bundles.py evidence
npx --yes pnpm@10.15.1 typecheck
npx --yes pnpm@10.15.1 --filter @replayforge/demo-bank test
npx --yes pnpm@10.15.1 build
# Measure domain coverage separately: Python tracing distorts the real OCR deadlines.
uv run pytest backend/tests/unit --cov=replayforge --cov-report=term-missing -q
uv run pytest backend/tests/integration -q
