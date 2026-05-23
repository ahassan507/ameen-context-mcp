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


if __name__ == "__main__":
    mcp.run()
