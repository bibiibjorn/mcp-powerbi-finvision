#!/usr/bin/env bash
# Installs the PBI + Fabric skills bundle into ~/.claude/.
# Non-destructive: existing same-named items are backed up before copy.
set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"

echo
echo "PBI + Fabric skills installer"
echo "Target: $CLAUDE_HOME"
echo

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$CLAUDE_HOME/backups/skills-bundle-import/$STAMP"

installed=0
backed_up=0

install_tree() {
  local subdir="$1"
  local src_base="$BUNDLE_ROOT/$subdir"
  [ -d "$src_base" ] || return 0
  local dst_base="$CLAUDE_HOME/$subdir"
  mkdir -p "$dst_base"

  local item name dst bdst
  for item in "$src_base"/*; do
    [ -e "$item" ] || continue
    name="$(basename "$item")"
    dst="$dst_base/$name"

    if [ -e "$dst" ]; then
      bdst="$BACKUP_DIR/$subdir/$name"
      mkdir -p "$(dirname "$bdst")"
      mv "$dst" "$bdst"
      backed_up=$((backed_up + 1))
      echo "  backed up existing $subdir/$name"
    fi

    cp -R "$item" "$dst"
    installed=$((installed + 1))
    echo "  installed $subdir/$name"
  done
}

install_tree skills
install_tree agents
install_tree rules

echo
echo "Done. $installed item(s) installed, $backed_up backed up."
[ "$backed_up" -gt 0 ] && echo "Backups: $BACKUP_DIR"
echo
echo "NEXT: set up the powerbi-finvision MCP server (see README.md) so the PBI"
echo "skills can actually connect to Power BI Desktop. Then restart Claude Code."
