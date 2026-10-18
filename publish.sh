#!/usr/bin/env bash
# Publishes to npm after the checks pass. Run `npm login` first and bump "version" in package.json.
set -euo pipefail
cd "$(dirname "$0")"
find . -path ./.venv -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
./test.sh
npm whoami >/dev/null || { echo "Run: npm login"; exit 1; }
npm publish --access public
