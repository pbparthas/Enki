"""uru.py — Core gate logic.

Called by hooks. Reads DB state. Returns allow/block decisions.
Three hard blocks (gates) + three nudges (non-blocking).

Gate 1: No Goal → No Code
Gate 2: No Approved Spec → No Agents (Standard/Full only)
Gate 3: Wrong Phase → No Code (phase must be >= implement)

Nudge 1: Unrecorded decision
Nudge 2: Long session without summary
Nudge 3: Unread kickoff mail
"""

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from enki.db import ENKI_ROOT, em_db, uru_db
from enki.gates.layer0 import (
    extract_db_targets,
    extract_write_targets,
    is_exempt,
    is_layer0_protected,
)

MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Task"}

# Phases where code changes are allowed
IMPLEMENT_PHASES = {"implement", "review", "ship"}

# Decision language patterns for nudge 1
DECISION_PATTERNS = [
    re.compile(r"\b(?:decided|choosing|going with|picked|selected)\b", re.I),
    re.compile(r"\b(?:approach|strategy|architecture|design decision)\b", re.I),
    re.compile(r"\b(?:trade-?off|instead of|rather than|over)\b", re.I),
]


def check_pre_tool_use(tool_name: str, tool_input: dict) -> dict:
    """Main gate check for pre-tool-use hook.

    Returns: {"decision": "allow"} or {"decision": "block", "reason": "..."}
    """
    try:
        if tool_name not in MUTATION_TOOLS and tool_name != "Bash":
            return {"decision": "allow"}
        if tool_name == "Task":
            project = _get_current_project() or str(Path.cwd())
            goal = _get_active_goal(project)
            if not goal:
                return {"decision": "block", "reason": "Set a goal with enki_goal before spawning agents."}
            # For Standard/Full, also check phase and spec
            tier = _get_tier(project)
            if tier in ("standard", "full"):
                phase = _get_current_phase(project)
                if phase and phase not in IMPLEMENT_PHASES:
                    return {"decision": "block", "reason": f"Phase is '{phase}'. Agent spawning needs phase >= implement."}
                if tier == "full":
                    if not _is_spec_approved(project):
                        return {"decision": "block", "reason": "Spec not approved. Full tier requires approved spec before agent spawning."}
        if tool_name in MUTATION_TOOLS:
            filepath = tool_input.get("file_path") or tool_input.get("path", "")
            targets = [filepath] if filepath else []
        elif tool_name == "Bash":
            command = tool_input.get("command", "")

            # Layer 0.5: DB protection
            db_targets = extract_db_targets(command)
            enki_root_str = str(ENKI_ROOT.resolve())
            for db in db_targets:
                db_resolved = str(Path(db).resolve())
                if db_resolved.startswith(enki_root_str):
                    _log_enforcement(
                        "pre-tool-use", "layer0.5", tool_name,
                        db, "block", "Direct DB manipulation"
                    )
                    return {
                        "decision": "block",
                        "reason": "Layer 0.5: Direct DB manipulation. Use Enki tools.",
                    }

            targets = extract_write_targets(command)
        else:
            targets = []

        if not targets:
            return {"decision": "allow"}

        for target in targets:
            if target == "__PYTHON_WRITE__":
                _log_enforcement(
                    "pre-tool-use", "layer0.5", tool_name,
                    target, "block", "Unverifiable Python write"
                )
                return {
                    "decision": "block",
                    "reason": "Unverifiable Python file write in bash command.",
                }

            if is_layer0_protected(target):
                _log_enforcement(
                    "pre-tool-use", "layer0", tool_name,
                    target, "block", f"Protected file {Path(target).name}"
                )
                return {
                    "decision": "block",
                    "reason": f"Layer 0: Protected file {Path(target).name}",
                }

            if is_exempt(target, tool_name):
                continue

            try:
                gate_result = _check_gates(target)
            except Exception:
                return {
                    "decision": "block",
                    "reason": "Gate check failed unexpectedly. Blocking by default.",
                }
            if gate_result["decision"] == "block":
                _log_enforcement(
                    "pre-tool-use", "layer1", tool_name,
                    target, "block", gate_result["reason"]
                )
                return gate_result

        return {"decision": "allow"}
    except Exception:
        return {
            "decision": "block",
            "reason": "Enforcement error. Blocking by default.",
        }


def check_post_tool_use(
    tool_name: str,
    tool_input: dict,
    assistant_response: str = "",
) -> dict:
    """Post-tool-use checks. Non-blocking. Returns nudge messages."""
    try:
        nudges = []
        session_id = _get_session_id()

        # Nudge 1: Unrecorded decision
        if assistant_response and _contains_decision_language(assistant_response):
            if not _recent_enki_remember(session_id, within_turns=2):
                if _should_fire_nudge("unrecorded_decision", session_id):
                    nudges.append(
                        "Good decision. Worth recording — consider enki_remember."
                    )
                    _record_nudge_fired("unrecorded_decision", session_id)

        # Nudge 2: Long session without summary
        tool_count = _get_tool_count(session_id)
        if tool_count > 30:
            if _should_fire_nudge("long_session", session_id):
                nudges.append(
                    f"Productive session — {tool_count} actions since last checkpoint. "
                    "Good time to capture state."
                )
                _record_nudge_fired("long_session", session_id)

        # Nudge 3: Unread kickoff mail
        if tool_name in ("Write", "Edit", "Bash"):
            unread = _get_unread_kickoff_mails()
            if unread:
                project = unread[0]
                if _should_fire_nudge("unread_kickoff", session_id):
                    nudges.append(
                        f"Kickoff mail pending for {project}. "
                        "Spawn EM to begin execution."
                    )
                    _record_nudge_fired("unread_kickoff", session_id)

        # Log tool call
        _log_enforcement(
            "post-tool-use", "nudge", tool_name,
            None, "allow", "; ".join(nudges) if nudges else None
        )

        if nudges:
            return {"decision": "allow", "nudges": nudges}
        return {"decision": "allow"}
    except Exception:
        return {
            "decision": "block",
            "reason": "Post-tool-use enforcement error. Blocking by default.",
        }


def init_session(session_id: str) -> None:
    """Initialize enforcement state for a new session."""
    session_path = ENKI_ROOT / "SESSION_ID"
    session_path.write_text(session_id)


def end_session(session_id: str) -> dict:
    """Write enforcement summary for session end."""
    try:
        with uru_db() as conn:
            stats = conn.execute(
                "SELECT action, COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ? GROUP BY action",
                (session_id,),
            ).fetchall()

            nudge_stats = conn.execute(
                "SELECT nudge_type, fire_count, acted_on FROM nudge_state "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchall()

        return {
            "session_id": session_id,
            "enforcement": {row["action"]: row["cnt"] for row in stats},
            "nudges": [
                {
                    "type": row["nudge_type"],
                    "fired": row["fire_count"],
                    "acted_on": row["acted_on"],
                }
                for row in nudge_stats
            ],
        }
    except Exception as e:
        raise RuntimeError("Failed to end enforcement session") from e


def inject_enforcement_context() -> str:
    """Build enforcement context string for post-compact injection."""
    project = _get_current_project()
    if not project:
        return "Uru: No active project."

    goal = _get_active_goal(project)
    phase = _get_current_phase(project)
    tier = _get_tier(project)

    lines = ["## Uru Enforcement State"]
    lines.append(f"- Project: {project}")
    lines.append(f"- Goal: {goal or 'NOT SET'}")
    lines.append(f"- Phase: {phase or 'NOT SET'}")
    lines.append(f"- Tier: {tier or 'NOT SET'}")

    if not goal:
        lines.append("- Gate 1: ACTIVE — set goal before editing code")
    if phase and phase not in IMPLEMENT_PHASES:
        lines.append(f"- Gate 3: ACTIVE — phase '{phase}' blocks code changes")
    if tier in ("standard", "full") and not _is_spec_approved(project):
        lines.append("- Gate 2: ACTIVE — spec needs approval")

    return "\n".join(lines)


# ── Private helpers ──


def _check_gates(filepath: str) -> dict:
    """Layer 1 gate checks. Only called for non-exempt files."""
    project = _get_current_project()

    if not project:
        return {
            "decision": "block",
            "reason": "Gate 1: No active project. Set one with enki_goal.",
        }

    goal = _get_active_goal(project)
    if not goal:
        return {
            "decision": "block",
            "reason": "Gate 1: No active goal. Set one with enki_goal.",
        }

    phase = _get_current_phase(project)
    if phase not in IMPLEMENT_PHASES:
        return {
            "decision": "block",
            "reason": f"Gate 3: Phase is '{phase}'. Code changes need phase >= implement.",
        }

    tier = _get_tier(project)
    if tier is None:
        return {
            "decision": "block",
            "reason": "Gate 2: Cannot determine tier. Blocking by default.",
        }
    if tier in ("standard", "full"):
        if not _is_spec_approved(project):
            return {
                "decision": "block",
                "reason": "Gate 2: No approved spec. Needs human approval before implementation.",
            }

    return {"decision": "allow"}


def _get_session_id() -> str:
    """Read current session ID from marker file."""
    session_path = ENKI_ROOT / "SESSION_ID"
    if session_path.exists():
        return session_path.read_text().strip()
    return "unknown"


def _get_current_project() -> str | None:
    """Get the current active project name."""
    projects_dir = ENKI_ROOT / "projects"
    if not projects_dir.exists():
        return None

    # Find project with most recent em.db activity
    latest = None
    latest_time = 0.0
    for proj_dir in projects_dir.iterdir():
        if proj_dir.is_dir():
            em_path = proj_dir / "em.db"
            if em_path.exists():
                mtime = em_path.stat().st_mtime
                if mtime > latest_time:
                    latest_time = mtime
                    latest = proj_dir.name

    return latest


def _get_active_goal(project: str) -> str | None:
    """Read active goal from em.db."""
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT task_name FROM task_state "
                "WHERE work_type = 'goal' AND status != 'completed' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row["task_name"] if row else None
    except Exception as e:
        raise RuntimeError("Failed to read active goal") from e


def _get_current_phase(project: str) -> str | None:
    """Read current phase from em.db."""
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT task_name FROM task_state "
                "WHERE work_type = 'phase' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row["task_name"] if row else None
    except Exception as e:
        raise RuntimeError("Failed to read current phase") from e


def _get_tier(project: str) -> str | None:
    """Read current tier from em.db."""
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT tier FROM task_state "
                "WHERE work_type = 'goal' AND status != 'completed' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row["tier"] if row else None
    except Exception as e:
        raise RuntimeError("Failed to read tier") from e


def _is_spec_approved(project: str) -> bool:
    """Check if implementation spec is approved in em.db."""
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT id FROM pm_decisions "
                "WHERE project_id = ? AND decision_type = 'spec_approval' "
                "AND human_response = 'approved' "
                "ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
            return row is not None
    except Exception as e:
        raise RuntimeError("Failed to check spec approval") from e


def _contains_decision_language(text: str) -> bool:
    """Check if text contains decision-like language."""
    return any(pattern.search(text) for pattern in DECISION_PATTERNS)


def _recent_enki_remember(session_id: str, within_turns: int = 2) -> bool:
    """Check if enki_remember was called recently in this session."""
    try:
        with uru_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ? AND tool_name = 'enki_remember' "
                "ORDER BY timestamp DESC LIMIT ?",
                (session_id, within_turns),
            ).fetchone()
            return row["cnt"] > 0
    except Exception as e:
        raise RuntimeError("Failed to check recent enki_remember") from e


def _should_fire_nudge(nudge_type: str, session_id: str) -> bool:
    """Check if a nudge should fire (graduated: less frequent over time)."""
    try:
        with uru_db() as conn:
            row = conn.execute(
                "SELECT fire_count, last_fired FROM nudge_state "
                "WHERE nudge_type = ? AND session_id = ?",
                (nudge_type, session_id),
            ).fetchone()

            if not row:
                return True

            # Graduate: first time immediately, then every 10 tool calls
            return row["fire_count"] < 3
    except Exception as e:
        raise RuntimeError("Failed to evaluate nudge firing") from e


def _record_nudge_fired(nudge_type: str, session_id: str) -> None:
    """Record that a nudge was fired."""
    try:
        with uru_db() as conn:
            conn.execute(
                "INSERT INTO nudge_state (nudge_type, session_id, last_fired, fire_count) "
                "VALUES (?, ?, datetime('now'), 1) "
                "ON CONFLICT(nudge_type, session_id) DO UPDATE SET "
                "last_fired = datetime('now'), fire_count = fire_count + 1",
                (nudge_type, session_id),
            )
    except Exception as e:
        raise RuntimeError("Failed to record nudge") from e


def _get_tool_count(session_id: str) -> int:
    """Get number of tool calls logged in this session."""
    try:
        with uru_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["cnt"]
    except Exception as e:
        raise RuntimeError("Failed to read tool count") from e


def _get_unread_kickoff_mails() -> list[str]:
    """Get projects with unread kickoff mails."""
    projects_dir = ENKI_ROOT / "projects"
    if not projects_dir.exists():
        return []

    results = []
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        em_path = proj_dir / "em.db"
        if not em_path.exists():
            continue
        try:
            with em_db(proj_dir.name) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mail_messages "
                    "WHERE to_agent = 'EM' AND status = 'unread' "
                    "AND subject LIKE '%kickoff%'",
                ).fetchone()
                if row and row["cnt"] > 0:
                    results.append(proj_dir.name)
        except Exception as e:
            raise RuntimeError("Failed to read kickoff mail") from e

    return results


def _log_enforcement(
    hook: str,
    layer: str,
    tool_name: str | None,
    target: str | None,
    action: str,
    reason: str | None,
) -> None:
    """Write enforcement log entry to uru.db."""
    session_id = _get_session_id()
    try:
        with uru_db() as conn:
            conn.execute(
                "INSERT INTO enforcement_log "
                "(id, session_id, hook, layer, tool_name, target, action, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    hook,
                    layer,
                    tool_name,
                    target,
                    action,
                    reason,
                ),
            )
    except Exception as e:
        raise RuntimeError("Failed to log enforcement") from e


# ── CLI entry point for hooks ──


def main():
    """Entry point when called from hooks: python -m enki.gates.uru"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--hook", required=True)
    parser.add_argument("--tool", default="")
    parser.add_argument("--input", default="{}")
    args = parser.parse_args()

    hook_input = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    tool_name = args.tool or hook_input.get("tool_name", "")
    tool_input = (
        json.loads(args.input) if args.input != "{}" else hook_input.get("tool_input", {})
    )

    if args.hook == "pre-tool-use":
        result = check_pre_tool_use(tool_name, tool_input)
    elif args.hook == "post-tool-use":
        response = hook_input.get("assistant_response", "")
        result = check_post_tool_use(tool_name, tool_input, response)
    elif args.hook == "session-start":
        session_id = hook_input.get("session_id", str(uuid.uuid4()))
        init_session(session_id)
        result = {"decision": "allow"}
    elif args.hook == "session-end":
        session_id = _get_session_id()
        result = end_session(session_id)
    else:
        result = {"decision": "allow"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
