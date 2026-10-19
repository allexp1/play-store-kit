#!/usr/bin/env bash
# Publishes to npm after the checks pass (logs in to npm if needed). Bump "version" in package.json.
set -euo pipefail
cd "$(dirname "$0")"
find . -path ./.venv -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
./test.sh
if ! npm whoami >/dev/null 2>&1; then
  echo "Not logged in to npm. Logging in now (approve it in the browser)..."
  npm login
  npm whoami >/dev/null 2>&1 || { echo "npm login did not finish. Run this script again." >&2; exit 1; }
fi
npm publish --access public
