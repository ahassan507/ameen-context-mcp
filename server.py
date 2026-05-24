"""
Ameen Hassan — Personal Context MCP Server
Reads from data/ files as source of truth. GitHub-backed.
"""

import json
import os
from datetime import datetime
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
    Search memory files in data/memories/ for entries matching the query string.
    Matches against filename and file content (case-insensitive).
    Returns matching file names and their content.
    """
    query_lower = query.lower()
    results = []

    for md_file in sorted(MEMORIES_DIR.glob("*.md")):
        content = md_file.read_text()
        if query_lower in md_file.name.lower() or query_lower in content.lower():
            results.append({"file": md_file.name, "content": content})

    if not results:
        return json.dumps({"found": 0, "results": []})
    return json.dumps({"found": len(results), "results": results}, indent=2)


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
    now = datetime.utcnow()
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
            if s.get("summary", "") in pending:
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

    fragment = question_fragment.lower()
    before = mem.get("open_threads", [])
    after = [t for t in before if fragment not in t.get("question", "").lower()]
    removed = len(before) - len(after)

    if removed == 0:
        return json.dumps({"removed": 0, "message": "No matching threads found."})

    mem["open_threads"] = after
    mem["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
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
    mem["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_project_memory(project, mem)
    return json.dumps({"updated": True, "project": project, "summary_length": len(summary)})


if __name__ == "__main__":
    mcp.run()
