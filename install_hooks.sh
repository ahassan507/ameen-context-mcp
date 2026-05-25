#!/bin/bash
# Run this yourself to install the AgentOS Session Memory hooks.
# It backs up ~/.claude/settings.json first.

set -e
SETTINGS="$HOME/.claude/settings.json"
BACKUP="$HOME/.claude/settings.json.bak.$(date +%s)"

cp "$SETTINGS" "$BACKUP"
echo "Backed up to: $BACKUP"

python3 - <<'PY'
import json
from pathlib import Path

p = Path.home() / ".claude" / "settings.json"
with open(p) as f:
    s = json.load(f)

s["hooks"] = {
    "SessionStart": [{
        "hooks": [{
            "type": "command",
            "command": "python3 '/Users/ameenhassan/Desktop/Tech & Coding/Coding_projects/ameen-context-mcp/hooks/session_start.py'"
        }]
    }],
    "SessionEnd": [{
        "hooks": [{
            "type": "command",
            "command": "python3 '/Users/ameenhassan/Desktop/Tech & Coding/Coding_projects/ameen-context-mcp/hooks/session_end.py'"
        }]
    }],
}

with open(p, "w") as f:
    json.dump(s, f, indent=2)

print("Hooks registered in", p)
PY

echo
echo "Done. Restart Claude Code to activate hooks."
echo "To revert: cp \"$BACKUP\" \"$SETTINGS\""

# ---------------------------------------------------------------------------
# Install launchd synthesis schedule (runs every Sunday at 02:00)
# ---------------------------------------------------------------------------
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$REPO_DIR/launchd/com.ameenhassan.agentos-synthesis.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.ameenhassan.agentos-synthesis.plist"

if [ -f "$PLIST_SRC" ]; then
    # Unload existing job if present (ignore error if not loaded)
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl load "$PLIST_DEST"
    echo "Synthesis schedule installed: runs every Sunday at 02:00."
    echo "  Plist: $PLIST_DEST"
    echo "  Log:   $REPO_DIR/data/_synthesis_launchd.log"
    echo "  To run now: launchctl start com.ameenhassan.agentos-synthesis"
    echo "  To uninstall: launchctl unload \"$PLIST_DEST\" && rm \"$PLIST_DEST\""
else
    echo "WARNING: launchd plist not found at $PLIST_SRC — skipping schedule install."
fi
