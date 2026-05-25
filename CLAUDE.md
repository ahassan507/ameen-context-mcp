# AgentOS / ameen-context-mcp — Claude Code Memory

## What This Project Is

The personal session memory infrastructure ("the vault") for all Claude Code sessions.
Every session auto-loads relevant project context at startup (SessionStart hook) and
auto-saves findings at close (SessionEnd hook). The MCP server exposes memory tools
to all sessions globally.

**Aliases:** AgentOS = "the vault" = "session memory system" = ameen-context-mcp

---

## Repository Structure

```
ameen-context-mcp/
├── server.py                        # FastMCP server — all MCP tool definitions
├── hooks/
│   ├── session_start.py             # SessionStart hook — classifies cwd, injects project memory
│   ├── session_end.py               # SessionEnd hook — captures transcript, queues for structuring
│   ├── structure_sessions.py        # LLM-based structuring pass (claude -p headless)
│   ├── synthesize_memory.py         # Memory synthesis + weekly digest + auto-push
│   ├── backfill_sessions.py         # Backfill pipeline for historical transcripts
│   └── run_synthesis.sh             # launchd wrapper for Sunday synthesis runs
├── launchd/
│   └── com.ameenhassan.agentos-synthesis.plist  # Registered Sunday 02:00 synthesis cron
├── data/
│   ├── projects/
│   │   ├── registry.json            # All registered projects (paths, keywords, default flag)
│   │   └── {project}/
│   │       ├── memory/project_memory.json   # Canonical project memory
│   │       └── sessions/            # Structured session JSON files
│   ├── digests/                     # Weekly cross-project digests
│   └── _structured_since_synthesis.json  # N-session synthesis trigger counter
└── install_hooks.sh                 # Registers hooks in ~/.claude/settings.json
```

---

## Current Version

**v3** — all components live.

| Component | Status |
|---|---|
| SessionStart hook (cwd classifier + memory injection) | Live |
| SessionEnd hook (transcript capture + queue) | Live |
| Auto-structuring pipeline (N=5 trigger) | Live |
| Synthesis pass (launchd Sunday 02:00 + N-session trigger) | Live |
| Auto-push to GitHub after synthesis | Live |
| Weekly digest (data/digests/) | Live |
| Mid-session memory tools (flag_decision, flag_blocker, note_solution) | Live |
| Default project fallback (agentos loads when cwd matches nothing) | Live |
| Resolution order injection in SessionStart | Live |

---

## MCP Tools (server.py)

Mid-session tools — call proactively, don't wait for session end:
- `context_flag_decision(project, decision, status)` — record a decision
- `context_flag_blocker(project, question)` — record an open question
- `context_note_solution(project, problem, solution)` — record a solved problem

Read tools:
- `context_get_project_memory(project)` — full project memory
- `context_search_memories(query)` — cross-project search
- `context_get_recent_sessions(project, limit)` — recent session summaries
- `context_list_projects()` — all registered projects

---

## Key Design Decisions

- Project classifier: cwd path matching (5pts) + keyword matching (1pt); fallback to default project
- `agentos` is the default project — loads when no other project matches the cwd
- Fingerprint guard in session_end.py blocks recursive meta-session processing
- Auto-push: `git add data/ && commit && push origin main` after every synthesis pass
- N-session trigger: synthesis fires automatically every 5 structured sessions
- 60-char prefix deduplication for decisions and open threads
- `source="mid-session"` tags distinguish real-time writes from session-end structured writes

---

## How to Run

```bash
# Start MCP server (normally auto-started by Claude Code)
python3 server.py

# Manual synthesis pass
python3 hooks/synthesize_memory.py

# Manual synthesis with weekly digest
python3 hooks/synthesize_memory.py --digest

# Manual synthesis without auto-push
python3 hooks/synthesize_memory.py --no-push

# Re-register hooks (after settings changes)
bash install_hooks.sh
```

---

## Development Notes

- All datetime: use `datetime.now(timezone.utc)` — never `datetime.utcnow()` (deprecated in Python 3.13)
- Hook stdout becomes `additionalContext` in Claude Code — keep output clean
- Hook stderr goes to logs — use for debug output
- Never modify `data/` manually during a synthesis pass — write contention risk
- `Python_test`, `bengal`, `somalia` are unrelated projects in the same parent directory
