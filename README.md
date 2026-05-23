# ameen-context-mcp

Personal context MCP server. Data files in `data/` are the source of truth — edit and commit them to keep context current.

## Tools

| Tool | Description |
|------|-------------|
| `context_get_identity` | Name, location, email, active roles |
| `context_get_preferences` | Communication style, coding/writing/budget standards |
| `context_get_work` | IDeaS internship context, sprint focus, tool stack |
| `context_get_grants` | Behavioral health work, 245G licensure, grant clients |
| `context_get_organizations` | All affiliated organizations |
| `context_get_skills` | Technical skills and tool proficiencies |
| `context_get_full_profile` | Everything combined |
| `context_search_memories` | Full-text search across memory files |
| `context_add_memory` | Save a new dated memory entry |

## Setup

```bash
pip install mcp
```

## Claude Desktop Config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ameen-context": {
      "command": "python3",
      "args": ["/Users/ameenhassan/Desktop/Tech & Coding/Coding_projects/ameen-context-mcp/server.py"]
    }
  }
}
```

## Claude Code Config

Add to `~/.claude/settings.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "ameen-context": {
      "command": "python3",
      "args": ["/Users/ameenhassan/Desktop/Tech & Coding/Coding_projects/ameen-context-mcp/server.py"]
    }
  }
}
```

## Updating Context

Edit `data/*.json` files directly, then commit and push. Memories accumulate in `data/memories/` as `YYYY-MM-DD_slug.md` files — writable by Claude via `context_add_memory` or manually.
