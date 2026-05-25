#!/usr/bin/env python3
"""
SessionStart hook (v2): classify cwd to a project, load project memory,
and emit a focused context block.

v2 improvements over v1:
  - Deduplicates open_threads before display
  - Shows recent structured session summaries
  - Surfaces playbooks if they exist
  - Compact output — prioritises signal over noise

Claude Code passes a JSON object on stdin:
  {"hook_event_name":"SessionStart","cwd":"/path","session_id":"...", ...}

Anything printed to stdout becomes additionalContext for the session.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"

PENDING_PLACEHOLDERS = {
    "(auto-captured by SessionEnd hook; pending structuring)",
    "(backfilled; pending structuring)",
}


def classify(cwd: str) -> tuple[str | None, int]:
    with open(REGISTRY_PATH) as f:
        registry = json.load(f)
    cwd_l = cwd.lower()
    scores = []
    default_project = None
    for proj in registry["projects"]:
        if proj.get("default"):
            default_project = proj["name"]
        score = 0
        for path in proj.get("paths", []):
            if path.lower() in cwd_l:
                score += 5
        for kw in proj.get("keywords", []):
            if kw.lower() in cwd_l:
                score += 1
        scores.append((proj["name"], score))
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores and scores[0][1] > 0:
        return scores[0]
    # No cwd match — fall back to the designated default project
    if default_project:
        return default_project, 0
    return None, 0


def load_recent_sessions(project: str, limit: int = 3) -> list[dict]:
    sessions_dir = PROJECTS_DIR / project / "sessions"
    if not sessions_dir.exists():
        return []
    files = sorted(sessions_dir.glob("*.json"), reverse=True)
    result = []
    for p in files:
        if len(result) >= limit:
            break
        try:
            s = json.loads(p.read_text())
            summary = s.get("summary", "")
            if any(summary.startswith(ph) for ph in PENDING_PLACEHOLDERS):
                continue
            if summary:
                result.append(s)
        except Exception:
            continue
    return result


def deduplicate_threads(threads: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped = []
    for t in threads:
        q = t.get("question", "").strip()
        key = q[:60].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped


def summarize_project(project: str) -> str:
    mem_path = PROJECTS_DIR / project / "memory" / "project_memory.json"
    if not mem_path.exists():
        return ""
    mem = json.loads(mem_path.read_text())

    lines = [f"## Session Memory — project: {project}"]

    # Alias map — surfaces known nicknames so the assistant doesn't search for them
    ALIASES = {
        "agentos": "AgentOS = 'the vault' = 'session memory system' = 'ameen-context-mcp'",
    }
    if project in ALIASES:
        lines.append(f"Aliases: {ALIASES[project]}")

    if mem.get("canonical_summary"):
        lines.append(f"Summary: {mem['canonical_summary']}")
    if mem.get("last_updated"):
        lines.append(f"Last updated: {mem['last_updated']}")

    # Playbooks (v2)
    playbooks = mem.get("playbooks", [])
    if playbooks:
        lines.append(f"\nEstablished patterns ({len(playbooks)}):")
        for pb in playbooks[:3]:
            lines.append(f"- [{pb.get('type','pattern')}] {pb.get('description','')[:100]} (seen {pb.get('frequency','?')}x)")

    # Known solutions
    solutions = mem.get("known_solutions", [])
    if solutions:
        lines.append(f"\nKnown solutions ({len(solutions)}):")
        for s in solutions[-5:]:
            lines.append(f"- {s.get('problem','')[:80]} → {s.get('solution','')[:80]}")

    # Decisions: show only confirmed/established, most recent
    decisions = [
        d for d in mem.get("decision_log", [])
        if d.get("status") in ("confirmed", "established")
    ]
    if decisions:
        lines.append(f"\nKey decisions ({len(decisions)} confirmed):")
        for d in decisions[-5:]:
            status = d.get("status", "?")
            badge = "✓✓" if status == "established" else "✓"
            lines.append(f"- [{badge}] {d.get('decision','')[:100]}")

    # Open threads (deduped)
    threads = deduplicate_threads(mem.get("open_threads", []))
    if threads:
        lines.append(f"\nOpen threads ({len(threads)}):")
        for t in threads[-5:]:
            lines.append(f"- {t.get('question','')[:100]}")

    # Recent session summaries (v2)
    recent = load_recent_sessions(project, limit=3)
    if recent:
        lines.append(f"\nRecent sessions:")
        for s in recent:
            sid = s.get("session_id", "?")
            summary = s.get("summary", "")[:120]
            lines.append(f"- [{sid}] {summary}")

    if len(lines) == 1:
        return ""

    # v3-A: mid-session tool instructions — project slug pre-filled so there's
    # zero friction. Claude should call these proactively, not just at session end.
    lines.append(f"""
Mid-session memory tools — call these proactively (project="{project}"):
- context_flag_decision("{project}", "decision text", "confirmed|tentative|reversed") → a decision is made
- context_flag_blocker("{project}", "question text") → an open question / blocker is identified
- context_note_solution("{project}", "problem", "solution") → a problem is solved or approach confirmed
These write directly to project memory — don't wait for session end.""")

    lines.append("\n(Loaded by AgentOS SessionStart hook v3.)")
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except Exception:
        payload = {}

    cwd = payload.get("cwd", "")
    if not cwd:
        return 0

    project, score = classify(cwd)
    if not project:
        return 0

    summary = summarize_project(project)
    if not summary:
        return 0

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
