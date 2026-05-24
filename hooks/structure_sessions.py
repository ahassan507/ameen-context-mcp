#!/usr/bin/env python3
"""
v2 Structuring Pass — processes raw auto-captured sessions using Claude CLI
to extract structured memory (summary, decisions, problems, results, tags).

Usage:
  python3 hooks/structure_sessions.py              # process all pending
  python3 hooks/structure_sessions.py --all        # reprocess all raw sessions
  python3 hooks/structure_sessions.py --file path  # process one session file

Requirements: claude CLI must be available in PATH (Claude Code subscription).
"""

import json
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROJECTS_DIR = DATA_DIR / "projects"
QUEUE_PATH = DATA_DIR / "_pending_structuring.jsonl"
DONE_PATH = DATA_DIR / "_structured.jsonl"
LOG_PATH = DATA_DIR / "_hook.log"


def log(msg: str) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.utcnow().isoformat()}Z structure: {msg}\n")


EXTRACT_PROMPT = """You are extracting structured session memory from a Claude Code conversation transcript.

Return ONLY a valid JSON object with these exact fields:
{{
  "summary": "5-10 sentence factual summary of what was discussed and accomplished",
  "problems": ["list of problems or questions discussed"],
  "approaches": ["list of approaches or solutions tried"],
  "results": ["list of outcomes, findings, or conclusions"],
  "decisions": [
    {{"decision": "what was decided", "status": "confirmed|tentative|reversed"}}
  ],
  "open_questions": ["unresolved questions remaining after the session"],
  "tags": ["lowercase-hyphenated", "topic", "tags"]
}}

Rules:
- Be factual. No filler. No invented data.
- If a field has nothing relevant, use an empty list.
- decisions.status must be exactly: confirmed, tentative, or reversed.
- tags must be lowercase and hyphenated for multi-word.

CONVERSATION TRANSCRIPT:
{excerpt}
"""


def extract_structure(excerpt: str) -> dict | None:
    if not excerpt or len(excerpt.strip()) < 100:
        return None

    prompt = EXTRACT_PROMPT.format(excerpt=excerpt[:6000])

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout.strip()
        if not output:
            log(f"claude returned empty output")
            return None

        # Extract JSON from output (handle markdown code blocks)
        json_match = re.search(r'\{[\s\S]+\}', output)
        if not json_match:
            log(f"no JSON found in claude output: {output[:200]}")
            return None

        return json.loads(json_match.group())
    except subprocess.TimeoutExpired:
        log("claude CLI timed out")
        return None
    except json.JSONDecodeError as e:
        log(f"JSON parse error: {e}")
        return None
    except FileNotFoundError:
        log("claude CLI not found in PATH")
        return None


def structure_file(session_path: Path) -> bool:
    with open(session_path) as f:
        session = json.load(f)

    # Skip if already structured
    placeholders = {
        "(auto-captured by SessionEnd hook; pending structuring)",
        "(backfilled; pending structuring)",
    }
    if session.get("summary") and session["summary"] not in placeholders:
        return True

    excerpt = session.get("raw_excerpt", "")
    if not excerpt:
        log(f"no raw_excerpt in {session_path.name}")
        return False

    log(f"structuring {session_path.name}...")
    structured = extract_structure(excerpt)
    if not structured:
        log(f"failed to structure {session_path.name}")
        return False

    # Update session with structured fields
    session["summary"] = structured.get("summary", session["summary"])
    session["problems"] = structured.get("problems", [])
    session["approaches"] = structured.get("approaches", [])
    session["results"] = structured.get("results", [])
    session["decisions"] = structured.get("decisions", [])
    session["open_questions"] = structured.get("open_questions", [])
    session["tags"] = structured.get("tags", [])
    session["structured_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(session_path, "w") as f:
        json.dump(session, f, indent=2)

    # Update project memory with extracted decisions/solutions
    project = session.get("project")
    if project:
        _merge_into_project_memory(project, session)

    log(f"structured {session_path.name} -> {len(session['decisions'])} decisions, {len(session['open_questions'])} open questions")
    return True


def _merge_into_project_memory(project: str, session: dict) -> None:
    mem_path = PROJECTS_DIR / project / "memory" / "project_memory.json"
    if not mem_path.exists():
        return

    with open(mem_path) as f:
        memory = json.load(f)

    sid = session["session_id"]
    changed = False

    if session["problems"] and session["results"]:
        memory.setdefault("known_solutions", []).append({
            "problem": session["problems"][0],
            "solution": session["results"][0],
            "evidence_sessions": [sid],
        })
        changed = True

    for d in session.get("decisions", []):
        memory.setdefault("decision_log", []).append({
            "decision": d.get("decision", ""),
            "status": d.get("status", "tentative"),
            "evidence_sessions": [sid],
        })
        changed = True

    for q in session.get("open_questions", []):
        memory.setdefault("open_threads", []).append({
            "question": q,
            "sessions": [sid],
        })
        changed = True

    tag_index = memory.setdefault("tag_index", {})
    for t in session.get("tags", []):
        tag_index.setdefault(t, []).append(sid)
        changed = True

    if changed:
        memory["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(mem_path, "w") as f:
            json.dump(memory, f, indent=2)


def process_queue() -> int:
    if not QUEUE_PATH.exists():
        print("Queue empty — nothing to process.")
        return 0

    done_ids = set()
    if DONE_PATH.exists():
        with open(DONE_PATH) as f:
            for line in f:
                try:
                    done_ids.add(json.loads(line)["session_path"])
                except Exception:
                    pass

    pending = []
    with open(QUEUE_PATH) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["session_path"] not in done_ids:
                    pending.append(entry)
            except Exception:
                pass

    if not pending:
        print("All queued sessions already structured.")
        return 0

    print(f"Processing {len(pending)} pending sessions...")
    success = 0
    for entry in pending:
        p = Path(entry["session_path"])
        if not p.exists():
            print(f"  SKIP (not found): {p.name}")
            continue
        ok = structure_file(p)
        if ok:
            with open(DONE_PATH, "a") as f:
                f.write(json.dumps({"session_path": str(p), "done_at": datetime.utcnow().isoformat()}) + "\n")
            print(f"  ✓ {p.name}")
            success += 1
        else:
            print(f"  ✗ {p.name}")

    print(f"\nDone: {success}/{len(pending)} structured.")
    return success


def process_all() -> int:
    session_files = list(PROJECTS_DIR.rglob("*/sessions/*.json"))
    placeholders = {"(auto-captured", "(backfilled"}
    raw = [p for p in session_files
           if any(json.loads(p.read_text()).get("summary", "").startswith(ph)
                  for ph in placeholders)]
    print(f"Found {len(raw)} unstructured sessions.")
    success = 0
    for p in raw:
        ok = structure_file(p)
        print(f"  {'✓' if ok else '✗'} {p.parent.parent.name}/{p.name}")
        if ok:
            success += 1
    print(f"\nDone: {success}/{len(raw)} structured.")
    return success


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--file" in args:
        idx = args.index("--file")
        p = Path(args[idx + 1])
        structure_file(p)
    elif "--all" in args:
        process_all()
    else:
        process_queue()
