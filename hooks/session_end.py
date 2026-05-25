#!/usr/bin/env python3
"""
SessionEnd / Stop hook (v2): capture session transcript and immediately
dispatch a background structuring pass via claude CLI.

Claude Code passes a JSON object on stdin with:
  - transcript_path: path to the session transcript JSONL
  - cwd, session_id, hook_event_name

Classified sessions (cwd matches a known project):
  - saved to data/projects/{project}/sessions/
  - queued + background structuring dispatched immediately

Unclassified sessions (cwd matches nothing in registry):
  - buffered to data/_unclassified/
  - cwd occurrence tracked in data/_unclassified_cwds.json
  - when the same cwd appears AUTO_REGISTER_THRESHOLD times, a new project
    is auto-registered in registry.json and all buffered sessions are moved

Meta-sessions (structuring / synthesis claude -p calls) are silently skipped
to prevent infinite recursive vaulting loops.
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"
QUEUE_PATH = DATA_DIR / "_pending_structuring.jsonl"
LOG_PATH = DATA_DIR / "_hook.log"
STRUCTURE_SCRIPT = REPO / "hooks" / "structure_sessions.py"
UNCLASSIFIED_DIR = DATA_DIR / "_unclassified"
UNCLASSIFIED_CWDS_PATH = DATA_DIR / "_unclassified_cwds.json"
AUTO_REGISTER_THRESHOLD = 2

# Fingerprints that identify AgentOS meta-sessions (structuring / synthesis
# claude -p calls). If any appear in the transcript excerpt we skip vaulting
# entirely — otherwise we get infinite recursive loops.
META_FINGERPRINTS = [
    "Return ONLY a valid JSON object with these exact fields",    # EXTRACT_PROMPT
    "You are extracting structured session memory",               # EXTRACT_PROMPT header
    "You are writing a 4-6 sentence canonical project summary",  # SUMMARY_PROMPT
]


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


# ---------------------------------------------------------------------------
# Option B: auto-registration of unclassified project directories
# ---------------------------------------------------------------------------

def cwd_to_slug(cwd: str) -> str:
    """Derive a lowercase-hyphenated project slug from the cwd basename."""
    name = Path(cwd).name or "unclassified"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unclassified"


def unique_slug(base: str, registry: dict) -> str:
    """Return base slug or base-2, base-3, ... if already taken in registry."""
    existing = {p["name"] for p in registry["projects"]}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def save_to_buffer(sid: str, session: dict, cwd: str) -> bool:
    """
    Buffer an unclassified session and increment the cwd occurrence counter.
    Returns True if AUTO_REGISTER_THRESHOLD is reached for this cwd.
    """
    UNCLASSIFIED_DIR.mkdir(parents=True, exist_ok=True)

    # Save raw session to unclassified buffer
    buf_path = UNCLASSIFIED_DIR / f"{sid}_unclassified.json"
    buffered = dict(session)
    buffered["_buffer_cwd"] = cwd
    with open(buf_path, "w") as f:
        json.dump(buffered, f, indent=2)

    # Load or init cwd counter
    cwds: dict = {}
    if UNCLASSIFIED_CWDS_PATH.exists():
        try:
            cwds = json.loads(UNCLASSIFIED_CWDS_PATH.read_text())
        except Exception:
            cwds = {}

    entry = cwds.setdefault(cwd, {"count": 0, "sessions": [],
                                   "first_seen": session["timestamp"]})
    entry["count"] += 1
    entry["sessions"].append(str(buf_path))
    entry["last_seen"] = session["timestamp"]

    UNCLASSIFIED_CWDS_PATH.write_text(json.dumps(cwds, indent=2))
    log(f"buffered unclassified session {sid} for cwd={cwd} (count={entry['count']})")

    return entry["count"] >= AUTO_REGISTER_THRESHOLD


def auto_register_project(cwd: str) -> str | None:
    """
    Auto-register a new project for a cwd that hit the threshold.
    - Adds entry to registry.json
    - Creates data/projects/{slug}/ folder structure
    - Moves all buffered sessions for this cwd into the new project
    - Queues them for structuring
    Returns the new project slug, or None on failure.
    """
    try:
        # Guard: don't re-register a cwd that was already processed.
        if UNCLASSIFIED_CWDS_PATH.exists():
            existing = json.loads(UNCLASSIFIED_CWDS_PATH.read_text())
            if existing.get(cwd, {}).get("registered_as"):
                log(f"auto_register_project: cwd={cwd} already registered as "
                    f"'{existing[cwd]['registered_as']}', skipping")
                return existing[cwd]["registered_as"]

        with open(REGISTRY_PATH) as f:
            registry = json.load(f)

        base_slug = cwd_to_slug(cwd)
        slug = unique_slug(base_slug, registry)
        dir_name = Path(cwd).name

        registry["projects"].append({
            "name": slug,
            # No auto-generated keywords — dir/basename words are substrings of
            # any unrelated cwd that happens to share the same folder name, which
            # causes false classification. Use only the full path for matching.
            # User can add real topic keywords later via registry.json.
            "keywords": [],
            "description": f"Auto-registered from unclassified sessions. Source: {cwd}",
            # Full cwd path (not basename) ensures only this exact directory matches.
            "paths": [cwd],
        })
        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2)

        # Create project folder structure
        proj_dir = PROJECTS_DIR / slug
        (proj_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (proj_dir / "memory").mkdir(parents=True, exist_ok=True)

        # Initialise project_memory.json
        mem_path = proj_dir / "memory" / "project_memory.json"
        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        mem = {
            "project": slug,
            "last_updated": now_str,
            "canonical_summary": (
                f"Auto-registered project for '{dir_name}'. "
                f"Source directory: {cwd}. "
                f"Run the synthesis pass to generate a real summary once sessions are structured."
            ),
            "known_solutions": [],
            "decision_log": [],
            "open_threads": [],
            "contributors": ["Ameen Hassan"],
            "tag_index": {"auto-registered": []},
        }
        mem_path.write_text(json.dumps(mem, indent=2))

        # Move buffered sessions into new project and queue for structuring
        cwds: dict = {}
        if UNCLASSIFIED_CWDS_PATH.exists():
            cwds = json.loads(UNCLASSIFIED_CWDS_PATH.read_text())

        moved = 0
        for buf_path_str in cwds.get(cwd, {}).get("sessions", []):
            buf_path = Path(buf_path_str)
            if not buf_path.exists():
                continue
            try:
                s = json.loads(buf_path.read_text())
                s["project"] = slug
                s.pop("_buffer_cwd", None)

                out_name = buf_path.name.replace("_unclassified", "_auto")
                out_path = proj_dir / "sessions" / out_name
                out_path.write_text(json.dumps(s, indent=2))

                with open(QUEUE_PATH, "a") as f:
                    f.write(json.dumps({
                        "session_path": str(out_path),
                        "project": slug,
                        "queued_at": now_str,
                    }) + "\n")

                buf_path.unlink()
                moved += 1

                # Dispatch background structuring immediately (same as classified path)
                try:
                    subprocess.Popen(
                        [sys.executable, str(STRUCTURE_SCRIPT), "--file", str(out_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception as popen_err:
                    log(f"structuring dispatch failed for {out_path.name}: {popen_err}")

            except Exception as e:
                log(f"failed to migrate buffer {buf_path.name}: {e}")

        # Mark cwd as registered in the tracker
        if cwd in cwds:
            cwds[cwd]["registered_as"] = slug
            cwds[cwd]["sessions"] = []
        UNCLASSIFIED_CWDS_PATH.write_text(json.dumps(cwds, indent=2))

        log(f"auto-registered project '{slug}' for cwd={cwd} (migrated {moved} sessions)")
        return slug

    except Exception as e:
        log(f"auto_register_project failed for cwd={cwd}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    # Guard: skip AgentOS meta-sessions to prevent recursive vaulting loops.
    for fingerprint in META_FINGERPRINTS:
        if fingerprint in excerpt:
            log(f"skipping meta-session (fingerprint: {fingerprint[:50]})")
            return 0

    # Build session ID early — needed on both classified and unclassified paths.
    now = datetime.utcnow()
    sid = now.strftime("%Y-%m-%d-%H%M%S")
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    project = classify(cwd, excerpt)

    if not project:
        # ── Unclassified path: buffer and maybe auto-register ──────────────
        session = {
            "session_id": sid,
            "timestamp": timestamp,
            "author": "Ameen Hassan",
            "project": None,
            "summary": "(auto-captured by SessionEnd hook; pending structuring)",
            "problems": [], "approaches": [], "results": [],
            "decisions": [], "open_questions": [],
            "tags": ["auto-captured", "unclassified"],
            "source_cwd": cwd,
            "source_session_id": session_id_in,
            "raw_excerpt": excerpt,
        }
        threshold_reached = save_to_buffer(sid, session, cwd)
        if threshold_reached:
            new_project = auto_register_project(cwd)
            if new_project:
                log(f"new project '{new_project}' registered for cwd={cwd}")
        return 0

    # ── Classified path: save, queue, dispatch structuring ─────────────────
    session = {
        "session_id": sid,
        "timestamp": timestamp,
        "author": "Ameen Hassan",
        "project": project,
        "summary": "(auto-captured by SessionEnd hook; pending structuring)",
        "problems": [], "approaches": [], "results": [],
        "decisions": [], "open_questions": [],
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

    # Also append to queue (so manual re-runs and --all still work)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps({
            "session_path": str(out_path),
            "project": project,
            "queued_at": timestamp,
        }) + "\n")

    log(f"saved {out_path.name} (project={project})")

    # v2: fire structuring in a detached background process.
    try:
        subprocess.Popen(
            [sys.executable, str(STRUCTURE_SCRIPT), "--file", str(out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log(f"dispatched background structuring for {out_path.name}")
    except Exception as e:
        log(f"background structuring dispatch failed: {e} — queued for manual run")

    return 0


if __name__ == "__main__":
    sys.exit(main())
