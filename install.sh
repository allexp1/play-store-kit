#!/usr/bin/env bash
# Installs play-store-kit into ~/.claude/skills/play-store-kit/ from a clone (same as: npx play-store-kit).
# Safe to re-run: an existing install is moved to ~/.claude/skill-backups/ first.
#   ./install.sh            install
#   ./install.sh --dry-run  show what would be copied, change nothing
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${HOME}/.claude/skills/play-store-kit"
BACKUP_ROOT="${HOME}/.claude/skill-backups"
PARTS=(SKILL.md scripts references templates .living requirements.txt)
if [[ "${1:-}" == "--dry-run" ]]; then
  echo "Would install play-store-kit to ${DEST}:"
  for part in "${PARTS[@]}"; do [[ -e "${SRC}/${part}" ]] && echo "  ${part}"; done
  exit 0
fi
echo "Installing play-store-kit"
echo "  source: ${SRC}"
echo "  dest:   ${DEST}"
if [[ -L "$DEST" ]]; then
  echo "  ${DEST} is a symlink (a development setup); leaving it alone."; exit 0
fi
if [[ -d "$DEST" ]]; then
  BACKUP="${BACKUP_ROOT}/play-store-kit-$(date +%Y%m%d-%H%M%S)"
  echo "  backing up existing install to ${BACKUP}"
  mkdir -p "$BACKUP_ROOT"; mv "$DEST" "$BACKUP"
fi
mkdir -p "$DEST"
for part in "${PARTS[@]}"; do
  [[ -e "${SRC}/${part}" ]] && cp -R "${SRC}/${part}" "${DEST}/${part}"
done
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "${DEST}/scripts/"*.py 2>/dev/null || true
echo; echo "Done. Restart Claude Code. Python 3.12 with: python3 -m pip install -r ${DEST}/requirements.txt"
