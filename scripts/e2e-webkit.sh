#!/usr/bin/env bash
# Run the Playwright suite in WebKit. The host lacks WebKit's system libraries, so this runs
# inside the local mcr.microsoft.com/playwright image (system deps) with the host's WebKit build.
# Usage: scripts/e2e-webkit.sh [playwright args]   (demo must be running on :8088)
set -euo pipefail
cd "$(dirname "$0")/../frontend"
exec podman run --rm --network host --userns=keep-id --security-opt label=disable \
  -v "$PWD":/w -v "$HOME/.cache/ms-playwright":/ms-pw:ro \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-pw -e HOME=/tmp -e COOKT_URL="${COOKT_URL:-http://127.0.0.1:8088}" \
  -w /w mcr.microsoft.com/playwright:v1.61.1-noble npx playwright test --project=webkit "$@"
