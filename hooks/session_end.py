#!/usr/bin/env python3
"""
SessionEnd / Stop hook: extract structured findings from the session transcript
and save to the project vault.

Claude Code passes a JSON object on stdin with:
  - transcript_path: path to the session transcript JSONL
  - cwd, session_id, hook_event_name

For v1 we do best-effort, no LLM call:
  - read last N messages of the transcript
  - classify by cwd to a project
  - write a raw session record with the transcript excerpt as summary
  - append a queue entry for later LLM-based structuring (v2)

The model can also call context_save_session directly during the session for
high-quality structured saves. This hook is the safety net.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"
QUEUE_PATH = DATA_DIR / "_pending_structuring.jsonl"
LOG_PATH = DATA_DIR / "_hook.log"


def log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"{datetime.utcnow().isoformat()}Z session_end: {msg}\n")
    except Exception:
        pass


def classify(cwd: str, content: str) -> str | None:
    with open(REGISTRY_PATH) as f:
        registry = json.load(f)
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


def read_transcript_excerpt(transcript_path: str, max_chars: int = 8000) -> str:
    p = Path(transcript_path)
    if not p.exists():
        return ""
    lines = []
    try:
        with open(p) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                role = entry.get("type") or entry.get("role") or ""
                msg = entry.get("message") or entry.get("content") or ""
                if isinstance(msg, dict):
                    msg = json.dumps(msg)[:400]
                elif isinstance(msg, list):
                    msg = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in msg
                    )[:400]
                else:
                    msg = str(msg)[:400]
                if role and msg:
                    lines.append(f"[{role}] {msg}")
    except Exception as e:
        log(f"read_transcript error: {e}")
        return ""
    text = "\n".join(lines)
    return text[-max_chars:]


def main() -> int:
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except Exception as e:
        log(f"stdin parse error: {e}")
        return 0

    transcript_path = payload.get("transcript_path", "")
    cwd = payload.get("cwd", "")
    session_id_in = payload.get("session_id", "")

    excerpt = read_transcript_excerpt(transcript_path) if transcript_path else ""
    if not excerpt and not cwd:
        return 0

    project = classify(cwd, excerpt)
    if not project:
        log(f"unclassified session at cwd={cwd}")
        return 0

    now = datetime.utcnow()
    sid = now.strftime("%Y-%m-%d-%H%M")

    # Write a raw session record (best-effort, unstructured summary)
    session = {
        "session_id": sid,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": "Ameen Hassan",
        "project": project,
        "summary": "(auto-captured by SessionEnd hook; pending structuring)",
        "problems": [],
        "approaches": [],
        "results": [],
        "decisions": [],
        "open_questions": [],
        "tags": ["auto-captured"],
        "source_cwd": cwd,
        "source_session_id": session_id_in,
        "raw_excerpt": excerpt,
    }

    out_dir = PROJECTS_DIR / project / "sessions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sid}_Ameen_Hassan_auto.json"
    with open(out_path, "w") as f:
        json.dump(session, f, indent=2)

    # Queue for later LLM-based structuring (v2)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps({
            "session_path": str(out_path),
            "project": project,
            "queued_at": session["timestamp"],
        }) + "\n")

    log(f"saved {out_path} (project={project})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
