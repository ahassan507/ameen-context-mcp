#!/usr/bin/env python3
"""
v3 Synthesis Pass — collapses repeated patterns, deduplicates threads,
refreshes summaries, auto-pushes vault to GitHub, and generates a
cross-project weekly digest.

Run periodically (after every ~5 sessions, or manually):
  python3 hooks/synthesize_memory.py                    # all projects + auto-push
  python3 hooks/synthesize_memory.py --project agentos  # one project + auto-push
  python3 hooks/synthesize_memory.py --summary-only     # only refresh summaries
  python3 hooks/synthesize_memory.py --digest           # also write weekly digest
  python3 hooks/synthesize_memory.py --no-push          # skip git push (offline)

What it does per project:
  1. Read all structured session files (skip pending/backfill)
  2. Find problems / approaches that repeat across 2+ sessions → playbooks
  3. Find decisions confirmed in 2+ sessions → mark as "established"
  4. Deduplicate open_threads (exact + near-duplicate by first 60 chars)
  5. Call claude -p to write a fresh canonical_summary from recent sessions
  6. Write updated project_memory.json

After all projects:
  7. git add data/ + commit + push to origin/main (skip with --no-push)
  8. Write data/_weekly_digest_{date}.md (only with --digest)
"""

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROJECTS_DIR = DATA_DIR / "projects"
LOG_PATH = DATA_DIR / "_hook.log"
DIGESTS_DIR = DATA_DIR / "digests"

PENDING_PLACEHOLDERS = {
    "(auto-captured by SessionEnd hook; pending structuring)",
    "(backfilled; pending structuring)",
}

SUMMARY_PROMPT = """You are writing a 4-6 sentence canonical project summary from recent session data.

Project: {project}
Existing summary: {existing_summary}

Recent session summaries (newest first):
{session_summaries}

Key decisions (confirmed):
{decisions}

Write a concise, factual canonical summary that captures the current state of this project.
Include: what it is, what's been built, what's in progress, what's next.
Return ONLY the summary text — no preamble, no markdown, no JSON.
"""


def log(msg: str) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}Z synthesize: {msg}\n")


def load_structured_sessions(project: str) -> list[dict]:
    """Load all structured (non-pending) session files for a project."""
    sessions_dir = PROJECTS_DIR / project / "sessions"
    if not sessions_dir.exists():
        return []
    sessions = []
    for p in sorted(sessions_dir.glob("*.json"), reverse=True):
        try:
            s = json.loads(p.read_text())
            summary = s.get("summary", "")
            if any(summary.startswith(ph) for ph in PENDING_PLACEHOLDERS):
                continue  # not yet structured
            if not summary:
                continue
            sessions.append(s)
        except Exception:
            continue
    return sessions


def deduplicate_threads(threads: list[dict]) -> list[dict]:
    """Remove duplicate open threads (exact match + prefix-60-char match)."""
    seen_exact: set[str] = set()
    seen_prefix: set[str] = set()
    deduped = []
    for t in threads:
        q = t.get("question", "").strip()
        key_exact = q.lower()
        key_prefix = q[:60].lower()
        if key_exact in seen_exact or key_prefix in seen_prefix:
            continue
        seen_exact.add(key_exact)
        seen_prefix.add(key_prefix)
        deduped.append(t)
    return deduped


def build_playbooks(sessions: list[dict]) -> list[dict]:
    """
    Find problems/approaches that appear across 2+ sessions.
    Returns a list of playbook entries.
    """
    problem_sessions: dict[str, list[str]] = defaultdict(list)
    approach_sessions: dict[str, list[str]] = defaultdict(list)

    for s in sessions:
        sid = s.get("session_id", "?")
        # Use sets per session to avoid counting within-session duplicates
        session_problems = {re.sub(r"[^a-z0-9 ]", "", p.lower())[:80]
                            for p in s.get("problems", [])}
        session_approaches = {re.sub(r"[^a-z0-9 ]", "", a.lower())[:80]
                              for a in s.get("approaches", [])}
        for key in session_problems:
            problem_sessions[key].append(sid)
        for key in session_approaches:
            approach_sessions[key].append(sid)

    playbooks = []

    # Problems seen in 2+ distinct sessions
    for key, sids in problem_sessions.items():
        if len(sids) >= 2:
            # Find the original (un-normalized) text
            original = ""
            for s in sessions:
                for p in s.get("problems", []):
                    if re.sub(r"[^a-z0-9 ]", "", p.lower())[:80] == key:
                        original = p
                        break
                if original:
                    break
            playbooks.append({
                "type": "recurring_problem",
                "description": original or key,
                "frequency": len(sids),
                "sessions": list(set(sids)),
            })

    # Approaches seen in 2+ distinct sessions
    for key, sids in approach_sessions.items():
        if len(sids) >= 2:
            original = ""
            for s in sessions:
                for a in s.get("approaches", []):
                    if re.sub(r"[^a-z0-9 ]", "", a.lower())[:80] == key:
                        original = a
                        break
                if original:
                    break
            playbooks.append({
                "type": "proven_approach",
                "description": original or key,
                "frequency": len(sids),
                "sessions": list(set(sids)),
            })

    # Sort by frequency descending
    playbooks.sort(key=lambda x: x["frequency"], reverse=True)
    return playbooks


def mark_established_decisions(memory: dict, sessions: list[dict]) -> dict:
    """
    If a decision appears confirmed in 2+ sessions, mark it as 'established'
    in the decision_log.
    """
    # Count confirmed decisions by normalized text
    decision_counts: Counter = Counter()
    for s in sessions:
        for d in s.get("decisions", []):
            if d.get("status") == "confirmed":
                key = d.get("decision", "")[:80].lower()
                decision_counts[key] += 1

    # Update decision_log
    for d in memory.get("decision_log", []):
        key = d.get("decision", "")[:80].lower()
        if decision_counts[key] >= 2 and d.get("status") != "established":
            d["status"] = "established"

    return memory


def refresh_canonical_summary(project: str, memory: dict, sessions: list[dict]) -> str | None:
    """Call claude CLI to generate a fresh canonical_summary."""
    if not sessions:
        return None

    recent = sessions[:5]
    summaries = "\n".join(
        f"- [{s.get('session_id', '?')}] {s.get('summary', '')[:300]}"
        for s in recent
    )
    confirmed = [
        d.get("decision", "")
        for d in memory.get("decision_log", [])
        if d.get("status") in ("confirmed", "established")
    ][-10:]
    decisions_text = "\n".join(f"- {d}" for d in confirmed) or "(none)"

    prompt = SUMMARY_PROMPT.format(
        project=project,
        existing_summary=memory.get("canonical_summary", "(none)"),
        session_summaries=summaries,
        decisions=decisions_text,
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout.strip()
        if output and len(output) > 20:
            return output
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"claude CLI error during summary refresh: {e}")
    return None


def synthesize_project(project: str, summary_only: bool = False) -> bool:
    mem_path = PROJECTS_DIR / project / "memory" / "project_memory.json"
    if not mem_path.exists():
        print(f"  SKIP {project} — no project_memory.json")
        return False

    memory = json.loads(mem_path.read_text())
    sessions = load_structured_sessions(project)

    if not sessions:
        print(f"  SKIP {project} — no structured sessions")
        return False

    print(f"  {project}: {len(sessions)} structured sessions")

    changed = False

    if not summary_only:
        # 1. Deduplicate open threads
        before = len(memory.get("open_threads", []))
        memory["open_threads"] = deduplicate_threads(memory.get("open_threads", []))
        after = len(memory["open_threads"])
        if before != after:
            print(f"    threads: {before} → {after} (removed {before - after} dupes)")
            changed = True

        # 2. Build playbooks
        playbooks = build_playbooks(sessions)
        if playbooks:
            memory["playbooks"] = playbooks
            print(f"    playbooks: {len(playbooks)} patterns identified")
            changed = True

        # 3. Mark established decisions
        old_log = json.dumps(memory.get("decision_log", []))
        memory = mark_established_decisions(memory, sessions)
        if json.dumps(memory.get("decision_log", [])) != old_log:
            print(f"    decisions: some marked as 'established'")
            changed = True

    # 4. Refresh canonical summary via claude CLI
    print(f"    refreshing canonical_summary via claude CLI...")
    new_summary = refresh_canonical_summary(project, memory, sessions)
    if new_summary:
        memory["canonical_summary"] = new_summary
        print(f"    summary refreshed ({len(new_summary)} chars)")
        changed = True
    else:
        print(f"    summary: claude CLI unavailable, keeping existing")

    if changed:
        memory["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(mem_path, "w") as f:
            json.dump(memory, f, indent=2)
        print(f"    ✓ written")

    return changed


def auto_push_vault() -> None:
    """
    Commit any data/ changes and push to origin/main.
    Silently skips if nothing changed or if push fails (no internet, etc.).
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # Stage all data/ changes (new sessions, updated memory, digests)
        subprocess.run(
            ["git", "-C", str(REPO), "add", "data/"],
            check=True, capture_output=True,
        )

        # Commit — exits non-zero with "nothing to commit", which we catch
        result = subprocess.run(
            ["git", "-C", str(REPO), "commit", "-m",
             f"chore: auto-sync vault {now_str}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                print("  auto-push: nothing to commit, vault already in sync")
                log("auto-push: nothing to commit")
                return
            # Some other commit error — log and bail before push
            log(f"auto-push commit failed: {result.stderr.strip()}")
            print(f"  auto-push: commit failed — {result.stderr.strip()[:120]}")
            return

        print(f"  auto-push: committed vault snapshot")

        # Push
        push = subprocess.run(
            ["git", "-C", str(REPO), "push", "origin", "main"],
            capture_output=True, text=True, timeout=30,
        )
        if push.returncode == 0:
            print("  auto-push: pushed to origin/main ✓")
            log("auto-push: success")
        else:
            print(f"  auto-push: push failed (offline?) — commit kept locally")
            log(f"auto-push: push failed: {push.stderr.strip()[:200]}")

    except subprocess.TimeoutExpired:
        print("  auto-push: push timed out — commit kept locally")
        log("auto-push: push timed out")
    except Exception as e:
        print(f"  auto-push: error — {e}")
        log(f"auto-push error: {e}")


def _load_sessions_since(cutoff: datetime) -> list[dict]:
    """
    Return all structured sessions across all projects with timestamp >= cutoff.
    Sorted newest first.
    """
    results = []
    for p in PROJECTS_DIR.iterdir():
        if not p.is_dir():
            continue
        sessions_dir = p / "sessions"
        if not sessions_dir.exists():
            continue
        for sf in sessions_dir.glob("*.json"):
            try:
                s = json.loads(sf.read_text())
                summary = s.get("summary", "")
                if any(summary.startswith(ph) for ph in PENDING_PLACEHOLDERS):
                    continue
                if not summary:
                    continue
                ts_str = s.get("timestamp", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    results.append(s)
            except Exception:
                continue
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results


def generate_weekly_digest() -> Path:
    """
    Write a markdown digest of the past 7 days to data/digests/digest_{date}.md.
    Returns the path to the written file.
    """
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    date_str = now.strftime("%Y-%m-%d")

    sessions = _load_sessions_since(week_start)

    # Aggregate per project
    by_project: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        proj = s.get("project") or "unclassified"
        by_project[proj].append(s)

    total_sessions = len(sessions)
    total_decisions = sum(
        len([d for d in s.get("decisions", []) if d.get("status") == "confirmed"])
        for s in sessions
    )
    total_problems = sum(len(s.get("problems", [])) for s in sessions)
    total_threads = sum(len(s.get("open_questions", [])) for s in sessions)

    lines = [
        f"# AgentOS Weekly Digest — Week of {week_start.strftime('%Y-%m-%d')}",
        f"",
        f"Generated: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"",
        f"## At a Glance",
        f"- **{total_sessions} session{'s' if total_sessions != 1 else ''}** across "
        f"{len(by_project)} project{'s' if len(by_project) != 1 else ''}",
        f"- **{total_decisions} decision{'s' if total_decisions != 1 else ''}** confirmed",
        f"- **{total_problems} problem{'s' if total_problems != 1 else ''}** surfaced",
        f"- **{total_threads} open thread{'s' if total_threads != 1 else ''}** added",
        f"",
    ]

    if not by_project:
        lines.append("_No structured sessions this week._")
    else:
        lines.append("## By Project")
        lines.append("")

        for proj in sorted(by_project.keys()):
            proj_sessions = by_project[proj]
            lines.append(f"### {proj} — {len(proj_sessions)} session{'s' if len(proj_sessions) != 1 else ''}")

            # Canonical summary excerpt
            mem_path = PROJECTS_DIR / proj / "memory" / "project_memory.json"
            if mem_path.exists():
                try:
                    mem = json.loads(mem_path.read_text())
                    summary = mem.get("canonical_summary", "")
                    if summary:
                        lines.append(f"> {summary[:240]}{'…' if len(summary) > 240 else ''}")
                        lines.append("")
                except Exception:
                    pass

            # Sessions this week
            lines.append("**Sessions this week:**")
            for s in proj_sessions:
                day = s.get("timestamp", "")[:10]
                snippet = s.get("summary", "")[:120]
                lines.append(f"- [{day}] {snippet}{'…' if len(s.get('summary','')) > 120 else ''}")
            lines.append("")

            # Decisions confirmed this week
            week_decisions = [
                d.get("decision", "")
                for s in proj_sessions
                for d in s.get("decisions", [])
                if d.get("status") == "confirmed"
            ]
            if week_decisions:
                lines.append("**Decisions confirmed:**")
                for d in week_decisions[:5]:
                    lines.append(f"- {d[:100]}")
                lines.append("")

            # New open threads this week
            week_threads = [
                q
                for s in proj_sessions
                for q in s.get("open_questions", [])
            ]
            if week_threads:
                lines.append("**New open threads:**")
                for q in week_threads[:5]:
                    lines.append(f"- {q[:100]}")
                lines.append("")

    # Cross-project open threads from project memory
    all_threads = []
    for p in PROJECTS_DIR.iterdir():
        if not p.is_dir():
            continue
        mem_path = p / "memory" / "project_memory.json"
        if not mem_path.exists():
            continue
        try:
            mem = json.loads(mem_path.read_text())
            for t in mem.get("open_threads", [])[-5:]:
                all_threads.append((p.name, t.get("question", "")))
        except Exception:
            continue

    if all_threads:
        lines.append("---")
        lines.append("")
        lines.append(f"## All Open Threads ({len(all_threads)} across all projects)")
        lines.append("")
        for proj, q in all_threads[-15:]:
            lines.append(f"- **[{proj}]** {q[:100]}")
        lines.append("")

    content = "\n".join(lines)
    out_path = DIGESTS_DIR / f"digest_{date_str}.md"
    out_path.write_text(content)
    log(f"digest written: {out_path.name} ({total_sessions} sessions, {len(by_project)} projects)")
    return out_path


def main():
    args = sys.argv[1:]
    summary_only = "--summary-only" in args
    do_digest = "--digest" in args
    do_push = "--no-push" not in args  # push by default unless --no-push
    target_project = None
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 >= len(args):
            print("Error: --project requires a project name argument.")
            sys.exit(1)
        target_project = args[idx + 1]

    if target_project:
        projects = [target_project]
    else:
        projects = [p.name for p in PROJECTS_DIR.iterdir()
                    if p.is_dir() and (p / "memory" / "project_memory.json").exists()]

    print(f"Synthesis pass — {len(projects)} project(s)")
    for proj in projects:
        synthesize_project(proj, summary_only=summary_only)

    if do_digest:
        print("\nGenerating weekly digest...")
        out_path = generate_weekly_digest()
        print(f"  digest written: {out_path}")

    if do_push:
        print("\nAuto-pushing vault to GitHub...")
        auto_push_vault()

    print("\nDone.")


if __name__ == "__main__":
    main()
