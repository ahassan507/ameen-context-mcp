"""
Ameen Hassan — Personal Context MCP Server
Reads from data/ files as source of truth. GitHub-backed.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ameen-context")

DATA_DIR = Path(__file__).parent / "data"
MEMORIES_DIR = DATA_DIR / "memories"
PROJECTS_DIR = DATA_DIR / "projects"
REGISTRY_PATH = PROJECTS_DIR / "registry.json"


def _load(filename: str) -> dict | list:
    path = DATA_DIR / filename
    with open(path) as f:
        return json.load(f)


def _fmt(data: dict | list) -> str:
    return json.dumps(data, indent=2)


@mcp.tool()
def context_get_identity() -> str:
    """Return Ameen's core identity: name, location, email, active roles."""
    return _fmt(_load("identity.json"))


@mcp.tool()
def context_get_preferences() -> str:
    """Return Ameen's working preferences: communication style, coding standards, writing standards."""
    return _fmt(_load("preferences.json"))


@mcp.tool()
def context_get_work() -> str:
    """Return Ameen's current work context: IDeaS internship, sprint focus, tool stack."""
    return _fmt(_load("work_context.json"))


@mcp.tool()
def context_get_grants() -> str:
    """Return Ameen's behavioral health and grant work context: Rooted Wellness, Uplift, BHC, PTR, 245G licensure."""
    return _fmt(_load("grant_context.json"))


@mcp.tool()
def context_get_organizations() -> str:
    """Return all organizations Ameen is affiliated with or advising."""
    return _fmt(_load("organizations.json"))


@mcp.tool()
def context_get_skills() -> str:
    """Return Ameen's technical skills and tool proficiencies."""
    return _fmt(_load("technical_skills.json"))


@mcp.tool()
def context_get_full_profile() -> str:
    """Return Ameen's complete profile: identity + work + grants + preferences. Use when broad context is needed."""
    profile = {
        "identity": _load("identity.json"),
        "work_context": _load("work_context.json"),
        "grant_context": _load("grant_context.json"),
        "preferences": _load("preferences.json"),
        "organizations": _load("organizations.json"),
        "technical_skills": _load("technical_skills.json"),
    }
    return _fmt(profile)


@mcp.tool()
def context_search_memories(query: str) -> str:
    """
    Search ALL memory sources for the query string (case-insensitive):
      - data/memories/*.md  (freeform memory notes)
      - Every project's canonical_summary, decision_log, open_threads,
        known_solutions, and recent session summaries

    Returns hits grouped by source so the caller knows which project each
    result came from.

    Args:
        query: keyword or phrase to search for
    """
    if not query or not query.strip():
        return json.dumps({"error": "query cannot be empty"})

    q = query.strip().lower()
    output: dict = {"query": query, "total_hits": 0, "memories": [], "projects": {}}

    # 1. data/memories/*.md
    for md_file in sorted(MEMORIES_DIR.glob("*.md")):
        content = md_file.read_text()
        if q in md_file.name.lower() or q in content.lower():
            output["memories"].append({"file": md_file.name, "excerpt": content[:400]})

    # 2. All project memories
    registry = _load_registry()
    for proj in registry.get("projects", []):
        name = proj["name"]
        mem = _load_project_memory(name)
        if not mem:
            continue

        hits: dict = {}

        # canonical_summary
        summary = mem.get("canonical_summary", "")
        if q in summary.lower():
            hits["canonical_summary"] = summary[:300]

        # decision_log
        matching_decisions = [
            d.get("decision", "") for d in mem.get("decision_log", [])
            if q in d.get("decision", "").lower()
        ]
        if matching_decisions:
            hits["decisions"] = matching_decisions[:5]

        # open_threads
        matching_threads = [
            t.get("question", "") for t in mem.get("open_threads", [])
            if q in t.get("question", "").lower()
        ]
        if matching_threads:
            hits["open_threads"] = matching_threads[:5]

        # known_solutions
        matching_solutions = [
            {"problem": s.get("problem", ""), "solution": s.get("solution", "")}
            for s in mem.get("known_solutions", [])
            if q in s.get("problem", "").lower() or q in s.get("solution", "").lower()
        ]
        if matching_solutions:
            hits["known_solutions"] = matching_solutions[:5]

        # recent session summaries
        sessions_dir = PROJECTS_DIR / name / "sessions"
        if sessions_dir.exists():
            matching_sessions = []
            pending = {"(auto-captured", "(backfilled"}
            for p in sorted(sessions_dir.glob("*.json"), reverse=True)[:20]:
                try:
                    s = json.loads(p.read_text())
                    sess_summary = s.get("summary", "")
                    if any(sess_summary.startswith(ph) for ph in pending):
                        continue
                    if q in sess_summary.lower():
                        matching_sessions.append({
                            "session_id": s.get("session_id"),
                            "summary": sess_summary[:200],
                        })
                        if len(matching_sessions) >= 3:
                            break
                except Exception:
                    continue
            if matching_sessions:
                hits["sessions"] = matching_sessions

        if hits:
            output["projects"][name] = hits

    output["total_hits"] = (
        len(output["memories"]) + sum(
            sum(len(v) if isinstance(v, list) else 1 for v in ph.values())
            for ph in output["projects"].values()
        )
    )

    if output["total_hits"] == 0:
        return json.dumps({"query": query, "found": 0, "results": []})

    return json.dumps(output, indent=2)


@mcp.tool()
def context_cross_project_threads(topic: str) -> str:
    """
    Find open threads across ALL projects that relate to a topic.
    Use when a blocker in one project might be answered by another project's
    memory, or to surface connected work across the portfolio.

    Returns threads grouped by project, with the source project labelled.

    Args:
        topic: keyword or phrase to search for in open threads
    """
    if not topic or not topic.strip():
        return json.dumps({"error": "topic cannot be empty"})

    q = topic.strip().lower()
    registry = _load_registry()
    matches: list[dict] = []

    for proj in registry.get("projects", []):
        name = proj["name"]
        mem = _load_project_memory(name)
        if not mem:
            continue
        for t in mem.get("open_threads", []):
            question = t.get("question", "")
            if q in question.lower():
                matches.append({
                    "project": name,
                    "question": question,
                    "recorded_at": t.get("recorded_at", t.get("sessions", ["?"])[0]
                                        if isinstance(t.get("sessions"), list) else "?"),
                    "source": t.get("source", "session-end"),
                })

    if not matches:
        return json.dumps({"topic": topic, "found": 0, "threads": []})

    # Group by project for readability
    grouped: dict = {}
    for m in matches:
        grouped.setdefault(m["project"], []).append(m["question"])

    return json.dumps({
        "topic": topic,
        "found": len(matches),
        "by_project": grouped,
        "all": matches,
    }, indent=2)


@mcp.tool()
def context_add_memory(title: str, content: str, tags: list[str] | None = None) -> str:
    """
    Save a new memory entry to data/memories/ as a dated markdown file.
    Use for decisions, discoveries, or context worth preserving across sessions.

    Args:
        title: Short slug-style title (e.g. "oerac-budget-approach")
        content: The memory body — what happened, decided, or was discovered
        tags: Optional list of topic tags (e.g. ["grants", "245G", "budget"])
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = title.lower().replace(" ", "-").replace("_", "-")
    filename = f"{date_str}_{slug}.md"
    filepath = MEMORIES_DIR / filename

    tag_line = ""
    if tags:
        tag_line = f"tags: [{', '.join(tags)}]\n"

    frontmatter = f"---\ntitle: {title}\ndate: {date_str}\n{tag_line}---\n\n"
    filepath.write_text(frontmatter + content)

    return json.dumps({"saved": filename, "path": str(filepath)})


# ---------------------------------------------------------------------------
# Session Memory tools (AgentOS layer)
# ---------------------------------------------------------------------------


def _load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def _project_memory_path(project: str) -> Path:
    return PROJECTS_DIR / project / "memory" / "project_memory.json"


def _load_project_memory(project: str) -> dict:
    path = _project_memory_path(project)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_project_memory(project: str, memory: dict) -> None:
    path = _project_memory_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(memory, f, indent=2)


def _ensure_project(project: str) -> None:
    """Create folder structure for a new project if missing, and add to registry."""
    proj_dir = PROJECTS_DIR / project
    (proj_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (proj_dir / "memory").mkdir(parents=True, exist_ok=True)
    mem_path = _project_memory_path(project)
    if not mem_path.exists():
        _write_project_memory(project, {
            "project": project,
            "last_updated": "",
            "canonical_summary": "",
            "known_solutions": [],
            "decision_log": [],
            "open_threads": [],
            "contributors": [],
            "tag_index": {},
        })
    registry = _load_registry()
    if not any(p["name"] == project for p in registry["projects"]):
        registry["projects"].append({
            "name": project,
            "keywords": [project.replace("-", " ")],
            "description": f"Auto-created project: {project}",
            "paths": [],
        })
        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2)


@mcp.tool()
def context_list_projects() -> str:
    """List all known projects with keywords and descriptions. Use to see what project slugs exist."""
    return _fmt(_load_registry())


@mcp.tool()
def context_classify_session(content: str, cwd: str = "") -> str:
    """
    Classify session content to a project using keyword + path matching.
    Returns the best-match project slug, plus scores for transparency.

    Args:
        content: session summary, transcript excerpt, or topic description
        cwd: optional current working directory of the session
    """
    registry = _load_registry()
    text = (content + " " + cwd).lower()
    scores = []
    for proj in registry["projects"]:
        score = 0
        for kw in proj.get("keywords", []):
            if kw.lower() in text:
                score += 2
        for path in proj.get("paths", []):
            if path.lower() in cwd.lower():
                score += 5
        scores.append({"project": proj["name"], "score": score})
    scores.sort(key=lambda x: x["score"], reverse=True)
    top = scores[0] if scores else {"project": None, "score": 0}
    return json.dumps({
        "best_match": top["project"] if top["score"] > 0 else None,
        "confidence": top["score"],
        "scores": scores,
    }, indent=2)


@mcp.tool()
def context_get_project_memory(project: str) -> str:
    """
    Return the full project memory file for a project: solutions, decisions,
    open threads, contributors, tags.

    Args:
        project: project slug (use context_list_projects to see options)
    """
    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory file for project '{project}'"})
    return _fmt(mem)


@mcp.tool()
def context_save_session(
    project: str,
    summary: str,
    problems: list[str] | None = None,
    approaches: list[str] | None = None,
    results: list[str] | None = None,
    decisions: list[dict] | None = None,
    open_questions: list[str] | None = None,
    tags: list[str] | None = None,
    author: str = "Ameen Hassan",
) -> str:
    """
    Save a structured session memory to a project's vault, and merge into project memory.

    Args:
        project: project slug. If new, the project is auto-created.
        summary: 5-10 sentence factual summary of the session.
        problems: problems discussed.
        approaches: approaches tried.
        results: outcomes and findings.
        decisions: list of {"decision": "...", "status": "confirmed|tentative|reversed"}.
        open_questions: unresolved questions.
        tags: lowercase tags for indexing.
        author: defaults to "Ameen Hassan".
    """
    _ensure_project(project)
    now = datetime.now(timezone.utc)
    session_id = now.strftime("%Y-%m-%d-%H%M")
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    session = {
        "session_id": session_id,
        "timestamp": timestamp,
        "author": author,
        "project": project,
        "summary": summary,
        "problems": problems or [],
        "approaches": approaches or [],
        "results": results or [],
        "decisions": decisions or [],
        "open_questions": open_questions or [],
        "tags": [t.lower() for t in (tags or [])],
    }

    # Write session file
    safe_author = author.replace(" ", "_")
    session_path = PROJECTS_DIR / project / "sessions" / f"{session_id}_{safe_author}.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with open(session_path, "w") as f:
        json.dump(session, f, indent=2)

    # Merge into project memory
    memory = _load_project_memory(project)
    memory.setdefault("project", project)
    memory["last_updated"] = timestamp

    # Append solutions (problem + first result as solution heuristic)
    if session["problems"] and session["results"]:
        memory.setdefault("known_solutions", []).append({
            "problem": session["problems"][0],
            "solution": session["results"][0],
            "evidence_sessions": [session_id],
        })

    # Append decisions
    for d in session["decisions"]:
        memory.setdefault("decision_log", []).append({
            "decision": d.get("decision", ""),
            "status": d.get("status", "tentative"),
            "evidence_sessions": [session_id],
        })

    # Append open threads (dedup — skip if same question already recorded)
    existing_q = {t.get("question", "").strip()[:60].lower()
                  for t in memory.get("open_threads", [])}
    for q in session["open_questions"]:
        key = q.strip()[:60].lower()
        if key not in existing_q:
            memory.setdefault("open_threads", []).append({
                "question": q,
                "sessions": [session_id],
            })
            existing_q.add(key)

    # Contributors
    contributors = set(memory.get("contributors", []))
    contributors.add(author)
    memory["contributors"] = sorted(contributors)

    # Tag index
    tag_index = memory.setdefault("tag_index", {})
    for t in session["tags"]:
        tag_index.setdefault(t, []).append(session_id)

    _write_project_memory(project, memory)

    return json.dumps({
        "saved": True,
        "session_id": session_id,
        "project": project,
        "session_path": str(session_path),
        "decisions_recorded": len(session["decisions"]),
        "open_questions_recorded": len(session["open_questions"]),
    }, indent=2)


@mcp.tool()
def context_get_recent_sessions(project: str, limit: int = 5) -> str:
    """
    Return the last N structured session summaries for a project.
    Skips sessions that are still pending structuring.

    Args:
        project: project slug
        limit: number of sessions to return (default 5)
    """
    sessions_dir = PROJECTS_DIR / project / "sessions"
    if not sessions_dir.exists():
        return json.dumps({"error": f"no sessions directory for '{project}'"})

    pending = {
        "(auto-captured by SessionEnd hook; pending structuring)",
        "(backfilled; pending structuring)",
    }
    files = sorted(sessions_dir.glob("*.json"), reverse=True)
    results = []
    for p in files:
        if len(results) >= limit:
            break
        try:
            s = json.loads(p.read_text())
            if any(s.get("summary", "").startswith(ph) for ph in pending):
                continue
            results.append({
                "session_id": s.get("session_id"),
                "timestamp": s.get("timestamp"),
                "summary": s.get("summary", "")[:500],
                "tags": s.get("tags", []),
                "decisions": len(s.get("decisions", [])),
                "open_questions": len(s.get("open_questions", [])),
            })
        except Exception:
            continue

    return json.dumps({"project": project, "count": len(results), "sessions": results}, indent=2)


@mcp.tool()
def context_resolve_thread(project: str, question_fragment: str) -> str:
    """
    Remove open threads whose question contains the given fragment (case-insensitive).
    Use after a question has been answered or is no longer relevant.

    Args:
        project: project slug
        question_fragment: substring to match against thread questions
    """
    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory for '{project}'"})

    if not question_fragment.strip():
        return json.dumps({"error": "question_fragment cannot be empty — would delete all threads."})

    fragment = question_fragment.strip().lower()
    before = mem.get("open_threads", [])
    after = [t for t in before if fragment not in t.get("question", "").lower()]
    removed = len(before) - len(after)

    if removed == 0:
        return json.dumps({"removed": 0, "message": "No matching threads found."})

    mem["open_threads"] = after
    mem["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_project_memory(project, mem)
    return json.dumps({"removed": removed, "remaining": len(after)}, indent=2)


@mcp.tool()
def context_update_canonical_summary(project: str, summary: str) -> str:
    """
    Overwrite the canonical_summary for a project.
    Use after a synthesis pass or when you have a better summary to save.

    Args:
        project: project slug
        summary: new canonical summary text
    """
    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory for '{project}'"})

    mem["canonical_summary"] = summary
    mem["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_project_memory(project, mem)
    return json.dumps({"updated": True, "project": project, "summary_length": len(summary)})


# ---------------------------------------------------------------------------
# Mid-session memory tools (v3-A) — call these proactively during a session
# ---------------------------------------------------------------------------

_MIN_TEXT_LEN = 10  # minimum chars for any decision / question / problem / solution


def _validate_text(field: str, value: str) -> str | None:
    """Return an error string if value fails QA, else None."""
    if not value or not value.strip():
        return f"{field} cannot be empty"
    if len(value.strip()) < _MIN_TEXT_LEN:
        return f"{field} too short ({len(value.strip())} chars, min {_MIN_TEXT_LEN})"
    return None


@mcp.tool()
def context_flag_decision(
    project: str,
    decision: str,
    status: str = "confirmed",
) -> str:
    """
    Immediately record a decision to project memory mid-session.
    Call this the moment a significant decision is made or confirmed — don't wait
    for session end.

    Args:
        project: project slug (shown in SessionStart context block)
        decision: what was decided, in one clear sentence
        status: "confirmed" | "tentative" | "reversed"
    """
    err = _validate_text("decision", decision)
    if err:
        return json.dumps({"error": err})

    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory for '{project}'"})

    valid_statuses = {"confirmed", "tentative", "reversed"}
    if status not in valid_statuses:
        return json.dumps({"error": f"status must be one of: {sorted(valid_statuses)}"})

    # Dedup: skip if same decision (first 60 chars, normalised) already in log
    key = decision.strip()[:60].lower()
    existing_keys = {
        d.get("decision", "").strip()[:60].lower()
        for d in mem.get("decision_log", [])
    }
    if key in existing_keys:
        return json.dumps({"saved": False, "duplicate": True,
                           "message": "Decision already recorded."})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mem.setdefault("decision_log", []).append({
        "decision": decision.strip(),
        "status": status,
        "recorded_at": timestamp,
        "source": "mid-session",
    })
    mem["last_updated"] = timestamp
    _write_project_memory(project, mem)

    return json.dumps({
        "saved": True,
        "project": project,
        "decision": decision.strip(),
        "status": status,
    })


@mcp.tool()
def context_flag_blocker(project: str, question: str) -> str:
    """
    Immediately record an open question or blocker to project memory mid-session.
    Call this when an unresolved question is identified that should persist beyond
    this session.

    Args:
        project: project slug (shown in SessionStart context block)
        question: the unresolved question or blocker, in one clear sentence
    """
    err = _validate_text("question", question)
    if err:
        return json.dumps({"error": err})

    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory for '{project}'"})

    # Dedup: skip if same question (60-char prefix) already in open_threads
    key = question.strip()[:60].lower()
    existing_keys = {
        t.get("question", "").strip()[:60].lower()
        for t in mem.get("open_threads", [])
    }
    if key in existing_keys:
        return json.dumps({"saved": False, "duplicate": True,
                           "message": "Blocker already recorded."})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mem.setdefault("open_threads", []).append({
        "question": question.strip(),
        "recorded_at": timestamp,
        "source": "mid-session",
    })
    mem["last_updated"] = timestamp
    _write_project_memory(project, mem)

    return json.dumps({
        "saved": True,
        "project": project,
        "question": question.strip(),
    })


@mcp.tool()
def context_note_solution(project: str, problem: str, solution: str) -> str:
    """
    Immediately record a problem→solution pair to project memory mid-session.
    Call this when a problem is solved or a working approach is confirmed so it's
    available in future sessions without re-deriving it.

    Args:
        project: project slug (shown in SessionStart context block)
        problem: the problem that was solved, in one clear sentence
        solution: how it was solved or what worked
    """
    for field, value in (("problem", problem), ("solution", solution)):
        err = _validate_text(field, value)
        if err:
            return json.dumps({"error": err})

    mem = _load_project_memory(project)
    if not mem:
        return json.dumps({"error": f"no memory for '{project}'"})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mem.setdefault("known_solutions", []).append({
        "problem": problem.strip(),
        "solution": solution.strip(),
        "recorded_at": timestamp,
        "source": "mid-session",
    })
    mem["last_updated"] = timestamp
    _write_project_memory(project, mem)

    return json.dumps({
        "saved": True,
        "project": project,
        "problem": problem.strip(),
        "solution": solution.strip(),
    })


if __name__ == "__main__":
    mcp.run()
