#!/bin/bash
# Wrapper for launchd: runs the AgentOS synthesis pass.
# Called by com.ameenhassan.agentos-synthesis.plist every Sunday at 02:00.

REPO="/Users/ameenhassan/Desktop/Tech & Coding/Coding_projects/ameen-context-mcp"

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

cd "$REPO" || exit 1

echo "=== AgentOS synthesis pass $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" >&2

/usr/local/bin/python3 hooks/synthesize_memory.py 2>&1
