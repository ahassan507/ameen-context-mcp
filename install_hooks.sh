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
