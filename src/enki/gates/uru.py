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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from enki.db import ENKI_ROOT, em_db, uru_db
from enki.gates.layer0 import (
    extract_db_targets,
    extract_write_targets,
    is_exempt,
    is_layer0_protected,
)
from enki.hook_versioning import check_hook_versions, format_hook_warning

MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Task"}

# Phase order and phases where code changes are allowed
PHASE_ORDER = ["intake", "debate", "spec", "approve", "implement", "review", "complete"]
IMPLEMENT_PHASES = {"implement", "review", "complete"}

# Decision language patterns for nudge 1
DECISION_PATTERNS = [
    re.compile(r"\b(?:decided|choosing|going with|picked|selected)\b", re.I),
    re.compile(r"\b(?:approach|strategy|architecture|design decision)\b", re.I),
    re.compile(r"\b(?:trade-?off|instead of|rather than|over)\b", re.I),
]

REASONING_BLOCK_PATTERNS = [
    re.compile(r"\b(?:disable|weaken|bypass)\s+(?:uru|layer\s*0|enforcement)\b", re.I),
    re.compile(r"\bmodify\s+(?:enforcement|gate|guard)\b", re.I),
]


@dataclass
class InspectionResult:
    blocked: bool
    reason: str | None = None
    pattern: str | None = None


def inspect_reasoning(reasoning_text: str | None) -> InspectionResult:
    """Inspect reasoning text for direct enforcement-bypass intent."""
    if not reasoning_text:
        return InspectionResult(blocked=False)

    for pattern in REASONING_BLOCK_PATTERNS:
        if pattern.search(reasoning_text):
            return InspectionResult(
                blocked=True,
                reason="Suspicious reasoning indicates enforcement bypass intent.",
                pattern="reasoning_bypass",
            )

    return InspectionResult(blocked=False)


def inspect_tool_input(tool_name: str, tool_input: dict) -> InspectionResult:
    """Inspect tool payload for enforcement-targeting behavior."""
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    path_lower = path.lower()
    command = str(tool_input.get("command") or "")
    command_lower = command.lower()

    if tool_name in MUTATION_TOOLS:
        if path and is_layer0_protected(path):
            return InspectionResult(
                blocked=True,
                reason=f"Layer 0: Protected file {Path(path).name}",
                pattern="enforcement_file_edit",
            )
        if ".claude/hooks/" in path_lower:
            return InspectionResult(
                blocked=True,
                reason="Layer 0: Attempting to modify hook scripts in .claude/hooks.",
                pattern="hook_modification",
            )
        if "sanitization" in path_lower:
            return InspectionResult(
                blocked=True,
                reason="Layer 0: Attempting to modify sanitization module/rules.",
                pattern="sanitization_bypass",
            )

    if tool_name == "Bash":
        sensitive_targets = (
            ".claude/hooks/",
            "scripts/hooks/",
            "uru.py",
            "sanitization",
            "verification.py",
            "layer0.py",
        )
        if re.search(r"\b(rm|chmod|mv|sed)\b", command_lower):
            if any(target in command_lower for target in sensitive_targets):
                return InspectionResult(
                    blocked=True,
                    reason="Suspicious bash command targets enforcement infrastructure.",
                    pattern="suspicious_bash",
                )

    return InspectionResult(blocked=False)


def check_pre_tool_use(
    tool_name: str,
    tool_input: dict,
    reasoning_text: str = "",
    hook_context: dict | None = None,
) -> dict:
    """Main gate check for pre-tool-use hook.

    Returns: {"decision": "allow"} or {"decision": "block", "reason": "..."}
    """
    try:
        hook_context = hook_context or {}

        goal_gate = _enforce_goal_gate(tool_name)
        if goal_gate:
            _log_enforcement(
                "pre-tool-use", "layer1", tool_name, None, "block", goal_gate["reason"]
            )
            return goal_gate

        tier_lock = _enforce_tier_immutability(tool_name, tool_input)
        if tier_lock:
            _log_enforcement(
                "pre-tool-use", "layer1", tool_name, None, "block", tier_lock["reason"]
            )
            return tier_lock

        reasoning_result = inspect_reasoning(reasoning_text)
        if reasoning_result.blocked:
            _log_enforcement(
                "pre-tool-use", "reasoning", tool_name,
                None, "block", reasoning_result.reason
            )
            return {
                "decision": "block",
                "reason": reasoning_result.reason,
            }

        tool_input_result = inspect_tool_input(tool_name, tool_input)
        if tool_input_result.blocked:
            target = str(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("command") or "")
            _log_enforcement(
                "pre-tool-use", "tool-input", tool_name,
                target, "block", tool_input_result.reason
            )
            return {
                "decision": "block",
                "reason": tool_input_result.reason,
            }

        if tool_name not in MUTATION_TOOLS and tool_name != "Bash":
            return {"decision": "allow"}
        if tool_name == "Task":
            project = _get_current_project() or str(Path.cwd())
            goal = _get_active_goal(project)
            if not goal:
                return {"decision": "block", "reason": "Set a goal with enki_goal before spawning agents."}
            # For Standard/Full, also check phase and spec
            tier = _get_tier(project) or "minimal"
            if tier in ("standard", "full"):
                phase = _get_current_phase(project)
                if not phase or phase not in IMPLEMENT_PHASES:
                    return {"decision": "block", "reason": f"Phase is '{phase or 'not set'}'. Agent spawning needs phase >= implement. Progress through the workflow first."}
                if tier == "full":
                    if not _is_spec_approved(project):
                        return {"decision": "block", "reason": "Spec not approved. Full tier requires approved spec before agent spawning."}
                prompt_check = _check_task_prompt_integrity(tool_input, hook_context)
                if prompt_check:
                    _log_enforcement(
                        "pre-tool-use", "layer1", tool_name, None, "block", prompt_check["reason"]
                    )
                    return prompt_check
                sequence_check = _check_task_agent_sequence(tool_input, hook_context)
                if sequence_check:
                    _log_enforcement(
                        "pre-tool-use", "layer1", tool_name, None, "block", sequence_check["reason"]
                    )
                    return sequence_check
                role = _extract_task_role(tool_input, hook_context)
                if role:
                    _set_agent_status(role, "in_progress")
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

            if _should_allow_main_context_non_implement_write(
                tool_name=tool_name,
                target=target,
                tool_input=tool_input,
                hook_context=hook_context or {},
            ):
                continue

            if _should_block_main_context_implementation_write(
                tool_name=tool_name,
                target=target,
                tool_input=tool_input,
                hook_context=hook_context or {},
            ):
                return {
                    "decision": "block",
                    "reason": "Direct implementation blocked. Use spawn_agent() and Task tool.",
                }

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
    hook_context: dict | None = None,
) -> dict:
    """Post-tool-use checks. Non-blocking. Returns nudge messages."""
    try:
        hook_context = hook_context or {}
        nudges = []
        session_id = _get_session_id()

        if tool_name == "Task":
            role = _extract_task_role(tool_input, hook_context)
            if role:
                status = "failed" if _task_call_failed(hook_context, assistant_response) else "completed"
                _set_agent_status(role, status)

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
            "decision": "allow",
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
    tier = _get_tier(project) or "minimal"

    lines = ["## Uru Enforcement State"]
    lines.append(f"- Project: {project}")
    lines.append(f"- Goal: {goal or 'NOT SET'}")
    lines.append(f"- Phase: {phase or 'NOT SET'}")
    lines.append(f"- Tier: {tier or 'NOT SET'}")

    if not goal:
        lines.append("- Gate 1: ACTIVE — set goal before editing code")
    if tier in ("standard", "full"):
        if not phase or phase not in IMPLEMENT_PHASES:
            lines.append(f"- Gate 3: ACTIVE — phase '{phase or 'NOT SET'}' blocks code changes")
        if not _is_spec_approved(project):
            lines.append("- Gate 2: ACTIVE — spec needs approval")
    else:
        if phase and phase not in IMPLEMENT_PHASES:
            lines.append(f"- Gate 3: ACTIVE — phase '{phase}' blocks code changes")
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
    tier = _get_tier(project) or "minimal"

    if tier in ("standard", "full"):
        if not phase or phase not in IMPLEMENT_PHASES:
            next_steps = "Progress through: intake → debate → spec → approve → implement"
            return {
                "decision": "block",
                "reason": f"Phase is '{phase or 'not set'}'. Code changes require phase >= implement. {next_steps}",
            }
        if not _is_spec_approved(project):
            return {
                "decision": "block",
                "reason": "Gate 2: No approved spec. Needs human approval before implementation.",
            }
    else:
        if phase and phase not in IMPLEMENT_PHASES:
            return {
                "decision": "block",
                "reason": f"Gate 3: Phase is '{phase}'. Code changes require phase >= implement.",
            }

    return {"decision": "allow"}


def _enforce_goal_gate(tool_name: str) -> dict | None:
    """Require an active goal before nearly all work begins."""
    if _is_goal_bootstrap_tool(tool_name):
        return None

    if _has_active_goal():
        return None

    return {
        "decision": "block",
        "reason": "No active goal. Set a goal with enki_goal before starting work.",
    }


def _is_goal_bootstrap_tool(tool_name: str) -> bool:
    """Allowlist tools before goal is set."""
    if tool_name == "Read":
        return True

    lowered = (tool_name or "").lower()
    return (
        lowered.endswith("enki_goal")
        or lowered.endswith("enki_recall")
        or lowered.endswith("enki_status")
    )


def _has_active_goal() -> bool:
    """Goal can come from session state or GOAL marker file."""
    try:
        project = _get_current_project()
        if project and _get_active_goal(project):
            return True
    except Exception:
        pass

    goal_file = ENKI_ROOT / "GOAL"
    if goal_file.exists() and goal_file.read_text().strip():
        return True

    return False


def _enforce_tier_immutability(tool_name: str, tool_input: dict) -> dict | None:
    """Tier cannot change mid-session once set by enki_goal."""
    try:
        project = _get_current_project()
        if not project:
            return None
        current_tier = _get_tier(project)
        if not current_tier:
            return None
    except Exception:
        return None

    candidate = None
    if tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        target = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if Path(target).name == "TIER":
            candidate = str(tool_input.get("content") or "").strip() or "<unknown>"
    elif tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        if re.search(r"\bTIER\b", command):
            candidate = "<unknown>"
    elif (tool_name or "").lower().endswith("enki_goal"):
        candidate = str(tool_input.get("tier") or "").strip() or None

    if candidate and candidate.lower() not in {current_tier.lower(), "auto"}:
        return {
            "decision": "block",
            "reason": f"Tier is locked for this session. Cannot change from {current_tier}.",
        }
    return None


def _check_task_prompt_integrity(tool_input: dict, hook_context: dict) -> dict | None:
    """Standard/Full Task calls must reference authored prompts."""
    prompt_blob = _collect_prompt_blob(tool_input, hook_context)
    prompt_root = str((ENKI_ROOT / "prompts").expanduser())
    normalized = prompt_blob.replace("~/.enki/prompts", prompt_root)

    role = _extract_task_role(tool_input, hook_context)
    if role:
        expected = str(Path(prompt_root) / f"{role}.md")
        if expected not in normalized:
            return {
                "decision": "block",
                "reason": (
                    "Task tool must reference an authored prompt from ~/.enki/prompts/. "
                    "Ad-hoc agent prompts are not allowed."
                ),
            }
        return None

    if "/.enki/prompts/" not in normalized:
        return {
            "decision": "block",
            "reason": (
                "Task tool must reference an authored prompt from ~/.enki/prompts/. "
                "Ad-hoc agent prompts are not allowed."
            ),
        }
    return None


def _collect_prompt_blob(tool_input: dict, hook_context: dict) -> str:
    """Gather possible prompt/description fields for integrity checks."""
    fields = []
    for src in (tool_input or {}, hook_context or {}):
        if not isinstance(src, dict):
            continue
        for key in ("prompt", "description", "task", "instructions", "input", "message"):
            value = src.get(key)
            if isinstance(value, str):
                fields.append(value)
        try:
            fields.append(json.dumps(src))
        except Exception:
            continue
    return "\n".join(fields)


def _check_task_agent_sequence(tool_input: dict, hook_context: dict) -> dict | None:
    """Enforce PM->Architect->(Dev/QA)->Validator ordering in Standard/Full."""
    role = _extract_task_role(tool_input, hook_context)
    if not role:
        return None

    try:
        project = _get_current_project()
        if not project:
            return None
        tier = _get_tier(project) or "minimal"
    except Exception:
        return None
    if tier not in {"standard", "full"}:
        return None

    deps = {
        "architect": ["pm"],
        "dev": ["architect"],
        "qa": ["architect"],
        "validator": ["dev", "qa"],
    }
    required = deps.get(role, [])
    if not required:
        return None

    # Retry logic: failed agents can be re-spawned.
    current = _get_agent_status(role)
    if current == "failed":
        return None

    for dep in required:
        dep_status = _get_agent_status(dep)
        if dep_status != "completed":
            return {
                "decision": "block",
                "reason": (
                    f"Agent sequence violation. {dep} must complete before {role} can be spawned."
                ),
            }
    return None


def _extract_task_role(tool_input: dict, hook_context: dict) -> str | None:
    """Extract normalized agent role for Task calls."""
    def _norm(value: str | None) -> str | None:
        if not value:
            return None
        v = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "general-purpose": None,
            "general_purpose": None,
        }
        if v in aliases:
            return aliases[v]
        return v

    for src in (tool_input or {}, hook_context or {}):
        if not isinstance(src, dict):
            continue
        for key in ("subagent_type", "agent_role", "role", "agent"):
            role = _norm(str(src.get(key) or ""))
            if role:
                return role

    text = _collect_prompt_blob(tool_input, hook_context).lower()
    match = re.search(r"/\.enki/prompts/([a-z_]+)\.md", text)
    if match:
        return _norm(match.group(1))
    return None


def _goal_id() -> str | None:
    """Fetch active goal task ID for agent status tracking."""
    try:
        project = _get_current_project()
        if not project:
            return None
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT task_id FROM task_state "
                "WHERE work_type = 'goal' AND status != 'completed' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row["task_id"] if row else None
    except Exception as e:
        raise RuntimeError("Failed to read active goal id") from e


def _get_agent_status(agent_role: str) -> str | None:
    """Read agent status for active goal."""
    gid = _goal_id()
    if not gid:
        return None
    try:
        with uru_db() as conn:
            row = conn.execute(
                "SELECT status FROM agent_status WHERE goal_id = ? AND agent_role = ?",
                (gid, agent_role),
            ).fetchone()
            return row["status"] if row else None
    except Exception as e:
        raise RuntimeError("Failed to read agent status") from e


def _set_agent_status(agent_role: str, status: str) -> None:
    """Upsert agent status for active goal."""
    gid = _goal_id()
    if not gid:
        return
    try:
        with uru_db() as conn:
            conn.execute(
                "INSERT INTO agent_status (goal_id, agent_role, status, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(goal_id, agent_role) DO UPDATE SET "
                "status = excluded.status, updated_at = datetime('now')",
                (gid, agent_role, status),
            )
    except Exception as e:
        raise RuntimeError("Failed to write agent status") from e


def _task_call_failed(hook_context: dict, assistant_response: str) -> bool:
    """Best-effort error detection for Task completion status."""
    for key in ("error", "tool_error", "exception"):
        value = hook_context.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if value is True:
            return True

    status = str(hook_context.get("status") or "").lower()
    if status in {"error", "failed", "failure"}:
        return True

    response_text = assistant_response.lower()
    if any(tok in response_text for tok in ("task failed", "error:", "exception")):
        return True
    return False


def _should_block_main_context_implementation_write(
    tool_name: str,
    target: str,
    tool_input: dict,
    hook_context: dict,
) -> bool:
    """Block direct main-context implementation writes in implement phase."""
    if tool_name not in {"Write", "Edit", "MultiEdit"}:
        return False
    if _is_main_impl_exempt_path(target):
        return False
    if not _is_src_or_test_path(target):
        return False
    if _is_subagent_context(tool_input, hook_context):
        return False

    project = _get_current_project()
    if not project:
        return False

    phase = _get_current_phase(project)
    return phase == "implement"


def _should_allow_main_context_non_implement_write(
    tool_name: str,
    target: str,
    tool_input: dict,
    hook_context: dict,
) -> bool:
    """Allow direct main-context src/test writes outside implement phase."""
    if tool_name not in {"Write", "Edit", "MultiEdit"}:
        return False
    if _is_main_impl_exempt_path(target):
        return True
    if not _is_src_or_test_path(target):
        return False
    if _is_subagent_context(tool_input, hook_context):
        return False

    project = _get_current_project()
    if not project:
        return False

    phase = _get_current_phase(project)
    return bool(phase and phase != "implement")


def _is_main_impl_exempt_path(filepath: str) -> bool:
    """Allow planning/spec/mail/config paths during any phase."""
    path = Path(filepath)
    parts = {p.lower() for p in path.parts}
    name = path.name.lower()

    try:
        path.resolve().relative_to(ENKI_ROOT.resolve())
        return True
    except ValueError:
        pass

    if "mail" in parts:
        return True

    if "specs" in parts or "plans" in parts:
        return True

    if "spec" in name or "plan" in name:
        return True

    return False


def _is_src_or_test_path(filepath: str) -> bool:
    """Match implementation/test trees targeted by this policy."""
    parts = {p.lower() for p in Path(filepath).parts}
    return "src" in parts or "test" in parts or "tests" in parts


def _is_subagent_context(tool_input: dict, hook_context: dict) -> bool:
    """Detect whether this tool call originates from a Task subagent context."""
    sources = [tool_input or {}, hook_context or {}]

    for src in sources:
        if src.get("subagent_type"):
            return True
        if src.get("agent_role"):
            return True
        if src.get("parent_tool_use_id") or src.get("parentToolUseId"):
            return True
        if src.get("task_id"):
            return True

        chain = src.get("tool_call_chain") or src.get("call_chain")
        if isinstance(chain, str) and "task" in chain.lower():
            return True
        if isinstance(chain, list):
            for item in chain:
                if isinstance(item, str) and "task" in item.lower():
                    return True
                if isinstance(item, dict):
                    tool = str(item.get("tool_name") or item.get("tool") or "")
                    if tool.lower() == "task":
                        return True

    return False


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

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    hook_input = json.loads(raw) if raw else {}
    tool_name = args.tool or hook_input.get("tool_name", "")
    tool_input = (
        json.loads(args.input) if args.input != "{}" else hook_input.get("tool_input", {})
    )

    if args.hook == "pre-tool-use":
        result = check_pre_tool_use(
            tool_name,
            tool_input,
            hook_context=hook_input,
        )
    elif args.hook == "post-tool-use":
        response = hook_input.get("assistant_response", "")
        result = check_post_tool_use(tool_name, tool_input, response, hook_context=hook_input)
    elif args.hook == "session-start":
        session_id = hook_input.get("session_id", str(uuid.uuid4()))
        init_session(session_id)
        result = {"decision": "allow"}
        version_result = check_hook_versions()
        if not version_result.all_current:
            warning = format_hook_warning(version_result)
            _log_enforcement(
                "session-start",
                "hook-version",
                None,
                None,
                "warn",
                warning,
            )
            result["warning"] = warning
            result["hook_version"] = {
                "mismatches": version_result.mismatches,
                "missing": version_result.missing,
            }
    elif args.hook == "session-end":
        session_id = _get_session_id()
        result = end_session(session_id)
    else:
        result = {"decision": "allow"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
