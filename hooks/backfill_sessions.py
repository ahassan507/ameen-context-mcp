#!/usr/bin/env python3
"""
Backfill — scans all Claude Code historical sessions in ~/.claude/projects/,
classifies each to a project, and saves raw captures to the vault.

Run once to import your entire session history:
  python3 hooks/backfill_sessions.py

Then run the structuring pass to extract decisions/summaries:
  python3 hooks/structure_sessions.py --all

Options:
  --dry-run   Show what would be imported without writing
  --limit N   Process only the first N sessions
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"
QUEUE_PATH = DATA_DIR / "_pending_structuring.jsonl"
LOG_PATH = DATA_DIR / "_hook.log"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def log(msg: str) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.utcnow().isoformat()}Z backfill: {msg}\n")


def load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def classify(cwd: str, content: str) -> str | None:
    registry = load_registry()
    text = (content + " " + cwd).lower()
    cwd_l = cwd.lower()
    scores = []
    for proj in registry["projects"]:
        score = 0
        for path in proj.get("paths", []):
            if path.lower() in cwd_l:
                score += 5
        for kw in proj.get("keywords", []):
            if kw.lower() in text:
                score += 1
        scores.append((proj["name"], score))
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores and scores[0][1] > 0:
        return scores[0][0]
    return None


def read_session(jsonl_path: Path) -> dict:
    """Extract cwd and message text from a Claude Code JSONL session file."""
    cwd = ""
    messages = []
    with open(jsonl_path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Extract cwd
            if not cwd and "cwd" in entry:
                cwd = entry["cwd"]

            # Extract message text
            role = entry.get("type") or entry.get("role") or ""
            content = entry.get("message") or entry.get("content") or ""

            if isinstance(content, dict):
                text = content.get("text") or json.dumps(content)[:300]
            elif isinstance(content, list):
                text = " ".join(
                    (c.get("text", "") if isinstance(c, dict) else str(c))
                    for c in content
                )[:400]
            else:
                text = str(content)[:400]

            if role in ("user", "assistant") and text.strip():
                label = "Human" if role == "user" else "Claude"
                messages.append(f"[{label}] {text.strip()}")

    excerpt = "\n".join(messages)[-8000:]
    return {"cwd": cwd, "excerpt": excerpt}


def already_saved(session_id_fragment: str) -> bool:
    """Check if a session with this ID fragment already exists in the vault."""
    for f in PROJECTS_DIR.rglob("*/sessions/*.json"):
        if session_id_fragment in f.name:
            return True
    return False


def save_raw(project: str, session_id: str, cwd: str, excerpt: str,
             source_file: str, dry_run: bool) -> bool:
    out_dir = PROJECTS_DIR / project / "sessions"
    out_path = out_dir / f"{session_id}_Ameen_Hassan_backfill.json"

    if out_path.exists():
        return False

    now = datetime.utcnow()
    session = {
        "session_id": session_id,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": "Ameen Hassan",
        "project": project,
        "summary": "(backfilled; pending structuring)",
        "problems": [],
        "approaches": [],
        "results": [],
        "decisions": [],
        "open_questions": [],
        "tags": ["backfill"],
        "source_cwd": cwd,
        "source_file": source_file,
        "raw_excerpt": excerpt,
    }

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(session, f, indent=2)
        with open(QUEUE_PATH, "a") as f:
            f.write(json.dumps({
                "session_path": str(out_path),
                "project": project,
                "queued_at": session["timestamp"],
            }) + "\n")

    return True


def main():
    dry_run = "--dry-run" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    if dry_run:
        print("DRY RUN — no files will be written.\n")

    jsonl_files = sorted(CLAUDE_PROJECTS.rglob("*.jsonl"))
    # Exclude subagent files (usually short/internal)
    jsonl_files = [f for f in jsonl_files if "subagents" not in str(f)]

    if limit:
        jsonl_files = jsonl_files[:limit]

    print(f"Found {len(jsonl_files)} session files to scan.\n")

    saved = 0
    skipped = 0
    unclassified = 0

    for jsonl_path in jsonl_files:
        session_uuid = jsonl_path.stem  # UUID filename

        # Derive a session_id timestamp from file mtime
        mtime = jsonl_path.stat().st_mtime
        sid = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d-%H%M")
        # Add UUID fragment to make unique
        sid = f"{sid}-{session_uuid[:8]}"

        if already_saved(session_uuid[:8]):
            print(f"  SKIP (exists): {session_uuid[:8]}")
            skipped += 1
            continue

        data = read_session(jsonl_path)
        cwd = data["cwd"]
        excerpt = data["excerpt"]

        project = classify(cwd, excerpt)
        if not project:
            print(f"  UNCLASSIFIED: {cwd or jsonl_path.parent.name}")
            unclassified += 1
            continue

        ok = save_raw(project, sid, cwd, excerpt, str(jsonl_path), dry_run)
        verb = "WOULD SAVE" if dry_run else ("SAVED" if ok else "SKIP")
        print(f"  {verb}: {session_uuid[:8]} → {project} (cwd: {cwd[-50:] if cwd else '?'})")
        if ok:
            saved += 1

    print(f"\n--- Backfill complete ---")
    print(f"Saved:        {saved}")
    print(f"Skipped:      {skipped}")
    print(f"Unclassified: {unclassified}")
    if not dry_run and saved > 0:
        print(f"\nRun the structuring pass to extract decisions and summaries:")
        print(f"  python3 hooks/structure_sessions.py --all")


if __name__ == "__main__":
    main()
