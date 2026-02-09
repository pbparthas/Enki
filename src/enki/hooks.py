"""Hook response generation for Claude Code integration."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from .session import (
    get_phase, get_tier, set_tier, get_session_edits, add_session_edit,
    tier_escalated, get_session_id,
)
from .enforcement import (
    check_all_gates, detect_tier, is_impl_file, GateResult,
)
from .violations import log_violation, log_escalation, log_escalation_to_file
from .persona import (
    build_session_start_injection,
    get_enki_greeting,
    generate_session_summary,
)


@dataclass
class HookResponse:
    """Response for a Claude Code hook."""
    decision: str  # "allow" or "block"
    reason: Optional[str] = None


def generate_hook_response(response: HookResponse) -> str:
    """Generate JSON response for hook."""
    if response.decision == "allow":
        return json.dumps({"decision": "allow"})
    else:
        return json.dumps({
            "decision": "block",
            "reason": response.reason or "Blocked by Enki",
        })


def handle_pre_tool_use(
    tool: str,
    input_data: dict,
    project_path: Optional[Path] = None,
) -> HookResponse:
    """Handle pre-tool-use hook.

    Checks all gates before allowing tool execution.

    Args:
        tool: Tool name (Edit, Write, Task, etc.)
        input_data: Tool input parameters
        project_path: Project path

    Returns:
        HookResponse with decision and reason
    """
    # Extract relevant data from input
    file_path = input_data.get("file_path")
    agent_type = input_data.get("subagent_type")

    # Check all gates
    result = check_all_gates(
        tool=tool,
        file_path=file_path,
        agent_type=agent_type,
        project_path=project_path,
    )

    if not result.allowed:
        # Log the violation
        log_violation(
            gate=result.gate or "unknown",
            tool=tool,
            reason=result.reason or "Gate blocked",
            file_path=file_path,
            project_path=project_path,
        )

        return HookResponse(
            decision="block",
            reason=result.reason,
        )

    return HookResponse(decision="allow")


def handle_post_tool_use(
    tool: str,
    input_data: dict,
    output_data: Optional[dict] = None,
    project_path: Optional[Path] = None,
) -> HookResponse:
    """Handle post-tool-use hook.

    Tracks edits and detects tier escalation.

    Args:
        tool: Tool name
        input_data: Tool input parameters
        output_data: Tool output (if any)
        project_path: Project path

    Returns:
        HookResponse (always allow for post-hook, but may include warnings)
    """
    # Track edits
    if tool in {"Edit", "Write", "MultiEdit"}:
        file_path = input_data.get("file_path")
        if file_path and is_impl_file(file_path):
            # Add to session edits
            add_session_edit(file_path, project_path)

            # Recalculate tier
            old_tier = get_tier(project_path)
            new_tier = detect_tier(project_path=project_path)

            if tier_escalated(old_tier, new_tier):
                # Log escalation
                log_escalation(old_tier, new_tier, project_path)
                log_escalation_to_file(old_tier, new_tier, project_path)

                # Update tier
                set_tier(new_tier, project_path)

    # Post-hook always allows (already executed)
    return HookResponse(decision="allow")


def handle_session_start(
    goal: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> dict:
    """Handle session start hook.

    Returns context to inject at session start.

    Args:
        goal: Optional session goal
        project_path: Project path

    Returns:
        Context dict with session info and relevant beads
    """
    from .session import start_session, get_session
    from .search import search

    # Start or get session
    session = get_session(project_path)
    if session is None:
        session = start_session(project_path, goal)

    context = {
        "session_id": session.session_id,
        "phase": session.phase,
        "tier": session.tier,
        "goal": session.goal,
        "edits": session.edits,
    }

    # Build Enki's context injection
    try:
        context["enki_greeting"] = get_enki_greeting(project_path)
        context["enki_context"] = build_session_start_injection(project_path)
    except Exception as e:
        logger.warning("Non-fatal error in hooks (session_start greeting): %s", e)
        context["enki_greeting"] = "What shall we work on?"
        context["enki_context"] = ""

    # Migrate per-project evolution (idempotent)
    try:
        from .evolution import migrate_per_project_evolution
        migrate_per_project_evolution(project_path or Path.cwd())
    except Exception as e:
        logger.warning("Non-fatal error in hooks (evolution migration): %s", e)
        pass

    # Load evolution context (local + global merged)
    try:
        from .evolution import get_evolution_context_for_session
        evo_context = get_evolution_context_for_session(project_path or Path.cwd())
        if evo_context:
            context["evolution_context"] = evo_context
    except Exception as e:
        logger.warning("Non-fatal error in hooks (evolution context): %s", e)
        pass

    # Surface feedback loop alerts
    try:
        from .feedback_loop import get_session_start_alerts
        alerts = get_session_start_alerts()
        if alerts:
            context["feedback_alerts"] = alerts
    except Exception as e:
        logger.warning("Non-fatal error in hooks (feedback alerts): %s", e)
        pass

    # Search for relevant beads if goal provided
    if goal:
        try:
            results = search(goal, limit=5, log_accesses=False)
            context["relevant_knowledge"] = [
                {
                    "type": r.bead.type,
                    "content": r.bead.summary or r.bead.content[:200],
                    "score": r.score,
                }
                for r in results
            ]
        except Exception as e:
            # Search might fail if embeddings not loaded
            logger.warning("Non-fatal error in hooks (bead search): %s", e)
            pass

    return context


def handle_session_end(project_path: Optional[Path] = None) -> dict:
    """Handle session end hook.

    Runs the full session-end pipeline:
    1. Reflector: extract learnings as beads (heuristic, no LLM)
    2. Feedback loop: propose enforcement adjustments (HITL only)
    3. Regression checks: flag applied proposals showing regression

    All steps degrade gracefully — archiving and summary work even
    if reflector/feedback_loop modules aren't available yet.

    Args:
        project_path: Project path

    Returns:
        Dict with summary, reflection report, feedback report, regressions
    """
    from .session import get_session

    session = get_session(project_path)
    if not session:
        return {"summary": "No active session."}

    result = {
        "session_id": session.session_id,
        "goal": session.goal,
        "phase": session.phase,
        "tier": session.tier,
        "files_edited": len(session.edits) if session.edits else 0,
    }

    # Loop 1: Reflect → store learnings as beads
    try:
        from .reflector import close_feedback_loop as reflect
        reflection_report = reflect(project_path)
        result["reflection"] = reflection_report
    except ImportError:
        result["reflection"] = {"status": "unavailable"}
    except Exception as e:
        result["reflection"] = {"status": f"error: {e}"}

    # Loop 2: Analyze → propose enforcement changes (never auto-apply)
    try:
        from .feedback_loop import run_feedback_cycle
        result["feedback"] = run_feedback_cycle(project_path)
    except ImportError:
        result["feedback"] = {"status": "module not available"}
    except Exception as e:
        result["feedback"] = {"status": f"error: {e}"}

    # Loop 3: Check regressions on previously applied proposals
    try:
        from .feedback_loop import check_for_regressions
        result["regressions"] = check_for_regressions()
    except ImportError:
        result["regressions"] = {"status": "module not available"}
    except Exception as e:
        result["regressions"] = {"status": f"error: {e}"}

    return result


def find_transcript(session_id: str) -> Optional[Path]:
    """Find CC's JSONL transcript file for a session.

    Searches ~/.claude/projects/ for the session JSONL.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None
    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        jsonl = project_dir / f"{session_id}.jsonl"
        if jsonl.exists():
            return jsonl
    return None


def read_jsonl_since_last_snapshot(
    transcript_path: Path,
    project_path: Path,
) -> list[dict]:
    """Read JSONL entries since the last snapshot marker.

    Returns all entries if no prior snapshot exists.
    """
    snapshot_path = project_path / ".enki" / "SNAPSHOT.json"
    last_count = 0
    if snapshot_path.exists():
        try:
            snapshots = json.loads(snapshot_path.read_text())
            if snapshots:
                # Count total entries processed in prior snapshots
                last_count = sum(len(s.get("entries", [])) for s in snapshots)
        except (json.JSONDecodeError, OSError):
            last_count = 0

    entries = []
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []

    # Return entries after what we've already processed
    # Cap at 200 entries per snapshot to bound memory
    return entries[last_count:][:200]


def extract_pre_compact_snapshot(
    project_path: Path,
    session_id: str,
) -> dict:
    """Extract structured snapshot from CC transcript before compaction.

    Reads the JSONL transcript (not CC's context window).
    Returns a JSON dict, NOT beads. Beads are created during
    end-of-session distillation.
    """
    from .session import get_phase, get_tier, get_goal

    transcript_path = find_transcript(session_id)
    if not transcript_path or not transcript_path.exists():
        return {"error": "transcript_not_found", "session_id": session_id}

    entries = read_jsonl_since_last_snapshot(transcript_path, project_path)

    snapshot = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": get_phase(project_path),
        "tier": get_tier(project_path),
        "goal": get_goal(project_path),
        "entries": [],
    }

    for entry in entries:
        # Extract thinking blocks (CC's reasoning — richest source)
        if entry.get("type") == "thinking":
            snapshot["entries"].append({
                "type": "thinking",
                "content": str(entry.get("thinking", ""))[:2000],
            })
        # Extract user messages (questions, corrections, decisions)
        elif entry.get("type") == "user" or entry.get("role") == "user":
            text = entry.get("content", entry.get("text", ""))
            if isinstance(text, str) and len(text) > 20:
                snapshot["entries"].append({
                    "type": "user",
                    "content": text[:1000],
                })
        # Extract tool calls (structural events)
        elif entry.get("type") == "tool_use":
            snapshot["entries"].append({
                "type": "tool_call",
                "tool": entry.get("name", ""),
                "summary": str(entry.get("input", ""))[:500],
            })

    # Write to disk — survives compaction
    enki_dir = project_path / ".enki"
    enki_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = enki_dir / "SNAPSHOT.json"
    snapshots = []
    if snapshot_path.exists():
        try:
            snapshots = json.loads(snapshot_path.read_text())
        except (json.JSONDecodeError, OSError):
            snapshots = []

    snapshots.append(snapshot)
    # Keep last 10 snapshots per session (bounded)
    snapshots = snapshots[-10:]
    snapshot_path.write_text(json.dumps(snapshots, indent=2))

    return snapshot


def parse_hook_input(stdin_data: str) -> dict:
    """Parse JSON input from Claude Code hook.

    Args:
        stdin_data: Raw stdin content

    Returns:
        Parsed dict with tool_name and tool_input
    """
    try:
        data = json.loads(stdin_data)
        return {
            "tool_name": data.get("tool_name", ""),
            "tool_input": data.get("tool_input", {}),
        }
    except json.JSONDecodeError:
        return {"tool_name": "", "tool_input": {}}


def format_escalation_warning(old_tier: str, new_tier: str, files: int, lines: int) -> str:
    """Format an escalation warning message."""
    return (
        f"⚠️ TIER ESCALATED: {old_tier} → {new_tier}\n\n"
        f"You've edited {files} files ({lines} lines).\n"
        f"This is no longer a {old_tier}.\n\n"
        f"Options:\n"
        f"1. Run /plan to create a spec for this work\n"
        f"2. Break into smaller changes\n\n"
        f"This escalation has been logged."
    )
