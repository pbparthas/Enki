"""parsing.py — Agent output JSON parsing + normalization + validation.

Strict JSON parsing with graduated retry:
- Attempt 1: "Output malformed. Return valid JSON per _base.md schema."
- Attempt 2: "Invalid JSON. Use template: [inject]."
- Attempt 3: Escalate to HITL. No further retries.

Also provides output normalization (merge defaults for missing fields)
and structural validation against _base.md schema.
"""

import json
import re

# Expected output template (from _base.md)
OUTPUT_TEMPLATE = {
    "agent": "",
    "task_id": "",
    "status": "DONE",
    "completed_work": "",
    "files_modified": [],
    "files_created": [],
    "decisions": [],
    "messages": [],
    "concerns": [],
    "blockers": [],
    "tests_run": 0,
    "tests_passed": 0,
    "tests_failed": 0,
}

REQUIRED_FIELDS = {"agent", "task_id", "status"}
VALID_STATUSES = {"DONE", "BLOCKED", "FAILED"}
LIST_FIELDS = {"files_modified", "files_created", "decisions", "messages",
               "concerns", "blockers"}
INT_FIELDS = {"tests_run", "tests_passed", "tests_failed"}


def parse_agent_output(raw_output: str) -> dict:
    """Parse agent output, extracting JSON from potentially noisy output.

    Tries three strategies in order:
    1. Direct JSON parse
    2. Extract from markdown code blocks
    3. Find outermost JSON object via brace matching

    Returns dict with 'success', 'parsed', and 'error' fields.
    """
    # Try direct parse first
    try:
        parsed = json.loads(raw_output.strip())
        return {"success": True, "parsed": parsed, "error": None}
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw_output, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1).strip())
            return {"success": True, "parsed": parsed, "error": None}
        except json.JSONDecodeError:
            pass

    # Try finding JSON object via balanced brace matching
    start = raw_output.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(raw_output)):
            ch = raw_output[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(raw_output[start:i + 1])
                        return {"success": True, "parsed": parsed, "error": None}
                    except json.JSONDecodeError:
                        break

    return {
        "success": False,
        "parsed": None,
        "error": "Could not extract valid JSON from output",
    }


def normalize_output(parsed: dict) -> dict:
    """Merge defaults from OUTPUT_TEMPLATE for any missing fields.

    Ensures all expected fields are present with correct types.
    Does not overwrite existing values.

    Args:
        parsed: The parsed JSON output from an agent.

    Returns:
        Normalized output dict with all template fields present.
    """
    result = OUTPUT_TEMPLATE.copy()
    result.update(parsed)

    # Ensure list fields are actually lists
    for field in LIST_FIELDS:
        if not isinstance(result.get(field), list):
            result[field] = []

    # Ensure int fields are actually ints
    for field in INT_FIELDS:
        val = result.get(field)
        if not isinstance(val, int):
            try:
                result[field] = int(val) if val is not None else 0
            except (ValueError, TypeError):
                result[field] = 0

    # Ensure status is uppercase
    if isinstance(result.get("status"), str):
        result["status"] = result["status"].upper()

    return result


def validate_output_structure(parsed: dict) -> dict:
    """Validate parsed output against _base.md schema.

    Checks:
    - Required fields present (agent, task_id, status)
    - Status is valid (DONE, BLOCKED, FAILED)
    - BLOCKED/FAILED has blockers
    - decisions have required subfields
    - messages have required subfields

    Returns dict with 'valid' bool and 'errors' list.
    """
    errors = []

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in parsed or not parsed[field]:
            errors.append(f"Missing required field: '{field}'")

    # Valid status
    status = parsed.get("status", "")
    if isinstance(status, str):
        status = status.upper()
    if status and status not in VALID_STATUSES:
        errors.append(f"Invalid status: '{status}'. Must be one of {VALID_STATUSES}")

    # BLOCKED/FAILED should have blockers
    if status in ("BLOCKED", "FAILED"):
        blockers = parsed.get("blockers", [])
        if not blockers:
            errors.append(f"Status is {status} but no blockers provided")

    # Validate decisions structure
    decisions = parsed.get("decisions", [])
    if isinstance(decisions, list):
        for i, d in enumerate(decisions):
            if isinstance(d, dict):
                if "decision" not in d:
                    errors.append(f"decisions[{i}] missing 'decision' field")
            else:
                errors.append(f"decisions[{i}] must be a dict")

    # Validate messages structure
    messages = parsed.get("messages", [])
    if isinstance(messages, list):
        for i, m in enumerate(messages):
            if isinstance(m, dict):
                if "to" not in m:
                    errors.append(f"messages[{i}] missing 'to' field")
                if "content" not in m:
                    errors.append(f"messages[{i}] missing 'content' field")
            else:
                errors.append(f"messages[{i}] must be a dict")

    # Validate concerns structure
    concerns = parsed.get("concerns", [])
    if isinstance(concerns, list):
        for i, c in enumerate(concerns):
            if isinstance(c, dict):
                if "content" not in c:
                    errors.append(f"concerns[{i}] missing 'content' field")
            else:
                errors.append(f"concerns[{i}] must be a dict")

    return {"valid": len(errors) == 0, "errors": errors}


def get_retry_prompt(attempt: int) -> str:
    """Get the appropriate retry prompt based on attempt number.

    Returns empty string if max retries exceeded (escalate to HITL).
    """
    if attempt == 1:
        return (
            "Output malformed. Return valid JSON per _base.md schema. "
            "No markdown wrapping. No preamble. ONLY JSON."
        )
    elif attempt == 2:
        template_str = json.dumps(OUTPUT_TEMPLATE, indent=2)
        return (
            f"Invalid JSON. Use this exact template:\n{template_str}\n"
            "Fill in your actual values. Return ONLY the JSON."
        )
    else:
        return ""  # Escalate to HITL


def extract_decisions(parsed_output: dict) -> list[dict]:
    """Extract decision records from parsed agent output."""
    return parsed_output.get("decisions", [])


def extract_messages(parsed_output: dict) -> list[dict]:
    """Extract mail messages from parsed agent output."""
    return parsed_output.get("messages", [])


def extract_concerns(parsed_output: dict) -> list[dict]:
    """Extract concerns from parsed agent output."""
    return parsed_output.get("concerns", [])


def extract_blockers(parsed_output: dict) -> list[str]:
    """Extract blockers from parsed agent output."""
    return parsed_output.get("blockers", [])


def extract_files_touched(parsed_output: dict) -> list[str]:
    """Get all files modified or created."""
    modified = parsed_output.get("files_modified", [])
    created = parsed_output.get("files_created", [])
    return list(set(modified + created))


def extract_test_results(parsed_output: dict) -> dict:
    """Extract test run/pass/fail counts."""
    return {
        "tests_run": parsed_output.get("tests_run", 0),
        "tests_passed": parsed_output.get("tests_passed", 0),
        "tests_failed": parsed_output.get("tests_failed", 0),
    }
