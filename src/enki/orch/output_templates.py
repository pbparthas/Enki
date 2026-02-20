"""output_templates.py — Strict JSON output schemas for every agent.

Standard template defines required fields. Per-agent variations add
role-specific fields. Parse failure escalation: retry → retry with
template → HITL.

All agent outputs are strict JSON. No markdown, no prose, no mixed format.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Standard output template — all agents must include these fields
STANDARD_TEMPLATE = {
    "agent": "",           # Agent role name
    "task_id": "",         # Task ID being worked on
    "status": "",          # DONE, BLOCKED, FAILED
    "summary": "",         # Brief summary of what was done
    "messages": [],        # Messages to route via mail
    "concerns": [],        # Issues to escalate
    "files_created": [],   # New files created
    "files_modified": [],  # Existing files modified
}

# Per-agent additional fields
AGENT_EXTENSIONS = {
    "dev": {
        "verification_commands": [],   # Commands to verify the work
        "tests_run": 0,
        "tests_passed": 0,
        "tests_failed": 0,
    },
    "qa": {
        "test_cases": [],              # List of test case definitions
        "coverage_summary": "",        # Coverage assessment
        "verification_commands": [],
        "tests_run": 0,
        "tests_passed": 0,
        "tests_failed": 0,
    },
    "architect": {
        "spec_sections": [],           # Implementation spec sections
        "acceptance_criteria": [],     # AC codes
        "tech_constraints": [],        # Technology constraints
    },
    "reviewer": {
        "issues": [],                  # Code review findings
        "approved": False,             # Overall approval
        "suggestions": [],             # Non-blocking suggestions
    },
    "infosec": {
        "vulnerabilities": [],         # Security findings
        "risk_level": "",              # low/medium/high/critical
        "recommendations": [],
        "approved": False,
    },
    "validator": {
        "spec_compliance": [],         # Compliance check results
        "missing_criteria": [],        # Unmet acceptance criteria
        "approved": False,
    },
    "devops": {
        "build_commands": [],          # Build/deploy commands
        "build_status": "",            # pass/fail
        "artifacts": [],               # Generated artifacts
        "verification_commands": [],
    },
    "dba": {
        "schema_changes": [],          # SQL DDL statements
        "migration_scripts": [],       # Migration file paths
        "data_concerns": [],           # Data integrity issues
    },
    "pm": {
        "decisions": [],               # PM decisions made
        "spec_updates": [],            # Spec modifications
        "stakeholder_actions": [],     # Actions requiring human input
    },
    "performance": {
        "benchmarks": [],              # Performance measurements
        "bottlenecks": [],             # Identified issues
        "recommendations": [],
    },
    "researcher": {
        "findings": [],                # Research findings
        "codebase_profile": {},        # Codebase analysis
        "tech_stack": {},              # Discovered tech stack
    },
    "ui_ux": {
        "components": [],              # UI components affected
        "design_decisions": [],        # Design choices made
        "accessibility_notes": [],     # A11y considerations
    },
}


def get_template(agent_role: str) -> dict:
    """Get the full output template for an agent role.

    Merges standard template with agent-specific extensions.
    """
    template = dict(STANDARD_TEMPLATE)
    extensions = AGENT_EXTENSIONS.get(agent_role, {})
    template.update(extensions)
    return template


def get_template_instruction(agent_role: str) -> str:
    """Get the JSON template as an instruction string for agent prompts.

    Returns a formatted instruction telling the agent what JSON to produce.
    """
    template = get_template(agent_role)
    json_str = json.dumps(template, indent=2)
    return (
        "Your output MUST be valid JSON matching this template exactly.\n"
        "Do not include any text before or after the JSON.\n"
        "Do not use markdown code fences.\n"
        f"\n{json_str}"
    )


def validate_output(raw_output: str, agent_role: str) -> dict:
    """Validate agent output against the template.

    Returns:
        {
            "valid": bool,
            "parsed": dict | None,
            "missing_fields": list[str],
            "error": str | None,
        }
    """
    # Parse JSON
    try:
        parsed = _parse_agent_json(raw_output)
    except (json.JSONDecodeError, ValueError) as e:
        return {
            "valid": False,
            "parsed": None,
            "missing_fields": [],
            "error": f"JSON parse error: {e}",
        }

    if not isinstance(parsed, dict):
        return {
            "valid": False,
            "parsed": None,
            "missing_fields": [],
            "error": "Output must be a JSON object",
        }

    # Check required standard fields
    required = {"agent", "task_id", "status"}
    missing = [f for f in required if f not in parsed]

    # Check status value
    status = str(parsed.get("status", "")).upper()
    valid_statuses = {"DONE", "BLOCKED", "FAILED"}
    if status and status not in valid_statuses:
        missing.append(f"status must be DONE/BLOCKED/FAILED (got: {status})")

    return {
        "valid": len(missing) == 0,
        "parsed": parsed,
        "missing_fields": missing,
        "error": f"Missing required fields: {missing}" if missing else None,
    }


def build_retry_prompt(
    original_prompt: str,
    error: str,
    agent_role: str,
    attempt: int,
) -> str:
    """Build retry prompt after parse/validation failure.

    Escalation: attempt 1 → gentle reminder, attempt 2 → full template, attempt 3+ → HITL
    """
    if attempt >= 3:
        return ""  # Caller should escalate to HITL

    if attempt == 1:
        return (
            f"{original_prompt}\n\n"
            f"IMPORTANT: Your previous output was not valid JSON.\n"
            f"Error: {error}\n"
            f"Output ONLY valid JSON. No markdown, no explanation."
        )

    # attempt 2: include full template
    template = get_template_instruction(agent_role)
    return (
        f"{original_prompt}\n\n"
        f"CRITICAL: Your output must be valid JSON.\n"
        f"Error from previous attempt: {error}\n\n"
        f"{template}"
    )


def _parse_agent_json(raw: str) -> Any:
    """Parse JSON from agent output, handling markdown fences."""
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    return json.loads(text)
