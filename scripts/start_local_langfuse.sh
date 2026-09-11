#!/usr/bin/env bash
set -euo pipefail

readonly LANGFUSE_TAG="v4.33.0"
readonly LANGFUSE_COMMIT="81bbfd169b72ea2ed53639699cc6632e8f908ce8"
readonly LANGFUSE_REPOSITORY="https://github.com/langfuse/langfuse.git"
readonly LOCAL_REPOSITORY=".local/langfuse"
readonly SERVER_ENV=".secrets/langfuse-server.env"
readonly OVERRIDE_FILE="config/langfuse-compose.override.yaml"
readonly REPLAYFORGE_UV_CACHE="${UV_CACHE_DIR:-/tmp/replayforge-uv-cache}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Engine and the Compose plugin are required." >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is required." >&2
  exit 2
fi

UV_CACHE_DIR="${REPLAYFORGE_UV_CACHE}" \
  uv run python scripts/bootstrap_langfuse_credentials.py

if [[ ! -d "${LOCAL_REPOSITORY}/.git" ]]; then
  git clone --depth 1 --branch "${LANGFUSE_TAG}" "${LANGFUSE_REPOSITORY}" "${LOCAL_REPOSITORY}"
fi
actual_commit="$(git -C "${LOCAL_REPOSITORY}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${LANGFUSE_COMMIT}" ]]; then
  echo "Local Langfuse checkout does not match the reviewed commit." >&2
  exit 3
fi

compose=(
  docker compose
  --project-name replayforge-langfuse
  --env-file "${SERVER_ENV}"
  --file "${LOCAL_REPOSITORY}/docker-compose.yml"
  --file "${OVERRIDE_FILE}"
)
"${compose[@]}" config --quiet
"${compose[@]}" up --detach

for _attempt in {1..60}; do
  if curl --fail --silent http://127.0.0.1:3100/api/public/ready >/dev/null; then
    UV_CACHE_DIR="${REPLAYFORGE_UV_CACHE}" uv run python scripts/check_local_langfuse.py
    exit 0
  fi
  sleep 5
done

echo "Langfuse did not become ready within five minutes." >&2
"${compose[@]}" ps >&2
exit 4
