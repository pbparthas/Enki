"""Hook response generation for Claude Code integration."""

import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass

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
    except Exception:
        context["enki_greeting"] = "What shall we work on?"
        context["enki_context"] = ""

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
        except Exception:
            # Search might fail if embeddings not loaded
            pass

    return context


def handle_session_end(project_path: Optional[Path] = None) -> dict:
    """Handle session end hook.

    Generates session summary and extracts learnings.

    Args:
        project_path: Project path

    Returns:
        Dict with summary and learnings
    """
    from .persona import generate_session_summary, extract_session_learnings
    from .beads import create_bead
    from .session import get_session

    session = get_session(project_path)
    if not session:
        return {"summary": "No active session."}

    summary = generate_session_summary(project_path)
    learnings = extract_session_learnings(project_path)

    # Optionally store learnings as beads
    stored_learnings = []
    for learning in learnings:
        try:
            bead = create_bead(
                content=learning["content"],
                bead_type=learning["type"],
                project=project_path.name if project_path else None,
                context=f"Session {session.session_id}",
                tags=[learning.get("category", "session")],
            )
            stored_learnings.append(bead.id)
        except Exception:
            pass

    return {
        "summary": summary,
        "learnings_extracted": len(learnings),
        "learnings_stored": stored_learnings,
    }


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
