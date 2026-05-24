#!/usr/bin/env python3
"""
SessionStart hook: classify the current cwd to a project, load that project's
memory, and emit a brief context block for Claude to read.

Claude Code passes a JSON object on stdin like:
  {"hook_event_name":"SessionStart","cwd":"/path","session_id":"...", ...}

Anything we print to stdout becomes additionalContext for the session.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"


def classify(cwd: str) -> tuple[str | None, int]:
    with open(REGISTRY_PATH) as f:
        registry = json.load(f)
    cwd_l = cwd.lower()
    scores = []
    for proj in registry["projects"]:
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
    return None, 0


def summarize_project(project: str) -> str:
    mem_path = PROJECTS_DIR / project / "memory" / "project_memory.json"
    if not mem_path.exists():
        return ""
    with open(mem_path) as f:
        mem = json.load(f)

    lines = [f"## Session Memory — project: {project}"]
    if mem.get("canonical_summary"):
        lines.append(f"Summary: {mem['canonical_summary']}")
    if mem.get("last_updated"):
        lines.append(f"Last updated: {mem['last_updated']}")

    solutions = mem.get("known_solutions", [])
    if solutions:
        lines.append(f"\nKnown solutions ({len(solutions)}):")
        for s in solutions[-5:]:
            lines.append(f"- {s.get('problem','')} → {s.get('solution','')}")

    decisions = mem.get("decision_log", [])
    if decisions:
        lines.append(f"\nRecent decisions ({len(decisions)}):")
        for d in decisions[-5:]:
            lines.append(f"- [{d.get('status','?')}] {d.get('decision','')}")

    threads = mem.get("open_threads", [])
    if threads:
        lines.append(f"\nOpen threads ({len(threads)}):")
        for t in threads[-5:]:
            lines.append(f"- {t.get('question','')}")

    if len(lines) == 1:
        return ""  # nothing useful to inject
    lines.append("\n(Loaded by AgentOS Session Memory hook.)")
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

    # Stdout becomes additionalContext per Claude Code hook spec
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
