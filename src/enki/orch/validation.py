"""validation.py — Blind validation + failure-mode checklist.

Validator checks output against specs. No stake in implementation.
Dev never sees tests. Tests verify, don't drive.
"""

import json

from enki.sanitization import sanitize_content


def validate_agent_output(output: str) -> dict:
    """Validate that agent output matches expected JSON format.

    Returns dict with 'valid' bool and parsed output or errors.
    """
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        return {"valid": False, "error": f"Invalid JSON: {e}", "parsed": None}

    required_fields = ["agent", "task_id", "status"]
    missing = [f for f in required_fields if f not in parsed]
    if missing:
        return {
            "valid": False,
            "error": f"Missing required fields: {missing}",
            "parsed": parsed,
        }

    valid_statuses = {"DONE", "BLOCKED", "FAILED"}
    if parsed.get("status") not in valid_statuses:
        return {
            "valid": False,
            "error": f"Invalid status: {parsed.get('status')}. Must be one of {valid_statuses}",
            "parsed": parsed,
        }

    return {"valid": True, "error": None, "parsed": parsed}


def failure_mode_checklist(task_output: dict) -> list[dict]:
    """Generate failure-mode checklist for a task.

    5-point mandatory checklist per task:
    1. Most likely failure point?
    2. How does the system detect it?
    3. Fastest path to safe state (rollback)?
    4. External dependency risks?
    5. Least certain assumption?
    """
    checklist = [
        {
            "question": "Most likely failure point?",
            "answer": task_output.get("blockers", ["Unknown"])[0] if task_output.get("blockers") else "No blockers identified",
        },
        {
            "question": "How does the system detect it?",
            "answer": f"Tests: {task_output.get('tests_run', 0)} run, {task_output.get('tests_failed', 0)} failed",
        },
        {
            "question": "Fastest path to safe state?",
            "answer": "Git revert to pre-task commit",
        },
        {
            "question": "External dependency risks?",
            "answer": _extract_external_deps(task_output),
        },
        {
            "question": "Least certain assumption?",
            "answer": task_output.get("concerns", [{"content": "None identified"}])[0].get("content", "None identified") if task_output.get("concerns") else "None identified",
        },
    ]
    return checklist


def check_spec_compliance(
    output: dict,
    spec_requirements: list[str],
) -> dict:
    """Check if output satisfies spec requirements.

    Returns dict with pass/fail and details.
    """
    completed_work = output.get("completed_work", "")
    files_modified = output.get("files_modified", [])
    files_created = output.get("files_created", [])

    results = []
    for req in spec_requirements:
        # Simple keyword check — real implementation would be more sophisticated
        found = req.lower() in completed_work.lower()
        results.append({
            "requirement": req,
            "satisfied": found,
        })

    satisfied = sum(1 for r in results if r["satisfied"])
    return {
        "total_requirements": len(spec_requirements),
        "satisfied": satisfied,
        "unsatisfied": len(spec_requirements) - satisfied,
        "pass": satisfied == len(spec_requirements),
        "details": results,
    }


def prepare_validator_context(
    project: str,
    task_id: str,
    dev_output: dict,
    qa_output: dict,
    tier: str,
) -> dict:
    """Assemble Validator prompt with blind wall (EM Spec §11, §19).

    Blind wall: Validator sees BOTH Dev and QA output, but Dev and QA
    never see each other's output. Validator compares them against spec.

    Args:
        project: Project name.
        task_id: Task being validated.
        dev_output: Developer's parsed output JSON.
        qa_output: QA's parsed output JSON.
        tier: Project tier (minimal/standard/full).

    Returns context dict for Validator agent prompt assembly.
    """
    context = {
        "role": "Validator",
        "task_id": task_id,
        "project": project,
        "tier": tier,
        "validation_type": "task",
    }

    # Dev side: code output (no QA reasoning)
    context["dev_submission"] = {
        "files_modified": dev_output.get("files_modified", []),
        "files_created": dev_output.get("files_created", []),
        "completed_work": dev_output.get("completed_work", ""),
        "decisions": dev_output.get("decisions", []),
    }

    # QA side: tests (no Dev reasoning)
    context["qa_submission"] = {
        "tests_run": qa_output.get("tests_run", 0),
        "tests_passed": qa_output.get("tests_passed", 0),
        "tests_failed": qa_output.get("tests_failed", 0),
        "test_results": qa_output.get("completed_work", ""),
    }

    # Failure-mode checklist (Standard + Full tiers only)
    if tier in ("standard", "full"):
        context["require_failure_mode_checklist"] = True
        context["failure_mode_template"] = [
            "Most likely failure point?",
            "How does the system detect it?",
            "Fastest path to safe state (rollback)?",
            "External dependency risks?",
            "Least certain assumption?",
        ]
    else:
        context["require_failure_mode_checklist"] = False

    return context


def prepare_redcell_context(
    project: str,
    impl_spec: str,
    tier: str,
) -> dict:
    """Adversarial review prompt — Full tier only, after Impl Spec (EM Spec §19).

    Red-cell review challenges the Implementation Spec before execution begins.
    Max 2 cycles of red-cell review before HITL escalation.

    Args:
        project: Project name.
        impl_spec: Implementation Spec text.
        tier: Must be "full" (no-op for other tiers).

    Returns context dict for red-cell agent, or empty dict if not applicable.
    """
    if tier != "full":
        return {"applicable": False, "reason": "Red-cell review is Full tier only"}

    return {
        "role": "RedCell",
        "project": project,
        "tier": tier,
        "applicable": True,
        "review_type": "adversarial",
        "impl_spec": impl_spec,
        "challenge_areas": [
            "Security vulnerabilities in the proposed architecture",
            "Scalability bottlenecks",
            "Missing error handling paths",
            "Assumptions that could fail under load",
            "Dependencies that could break",
            "Edge cases not covered in spec",
        ],
        "max_cycles": 2,
        "output_format": {
            "findings": "list of {severity, area, description, recommendation}",
            "overall_risk": "low|medium|high|critical",
            "proceed": "yes|no|conditional",
        },
    }


MAX_PARSE_ATTEMPTS = 3


def handle_parse_failure(
    project: str,
    task_id: str,
    raw_output: str,
    attempt: int,
) -> dict:
    """3-attempt retry with escalating hints, then HITL (EM Spec §19).

    Attempt 1: "Output malformed. Return valid JSON per schema."
    Attempt 2: "Invalid JSON. Use template: [inject template]."
    Attempt 3: Escalate to HITL. No further retries.

    Args:
        project: Project name.
        task_id: Task that produced bad output.
        raw_output: The unparseable output.
        attempt: Current attempt number (1-3).

    Returns dict with retry_prompt or escalation info.
    """
    from enki.orch.parsing import get_retry_prompt, OUTPUT_TEMPLATE

    if attempt >= MAX_PARSE_ATTEMPTS:
        return {
            "status": "hitl_escalation",
            "task_id": task_id,
            "reason": f"Agent output unparseable after {MAX_PARSE_ATTEMPTS} attempts",
            "raw_output_preview": raw_output[:500],
            "action": "Human must review raw output and manually extract results",
        }

    retry_prompt = get_retry_prompt(attempt)

    return {
        "status": "retry",
        "task_id": task_id,
        "attempt": attempt,
        "max_attempts": MAX_PARSE_ATTEMPTS,
        "retry_prompt": retry_prompt,
    }


def prepare_sprint_reviewer_context(
    project: str,
    sprint_id: str,
) -> dict:
    """Cross-task consistency check at sprint level (EM Spec §19).

    Sprint-level Reviewer looks at all completed tasks in a sprint
    for consistency: naming conventions, API contracts, shared patterns.

    Args:
        project: Project name.
        sprint_id: Sprint to review.

    Returns context dict for sprint Reviewer agent.
    """
    from enki.orch.task_graph import get_sprint_tasks

    def _sanitize_nested(value):
        if isinstance(value, dict):
            return {k: _sanitize_nested(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_sanitize_nested(v) for v in value]
        if isinstance(value, str):
            return sanitize_content(value, "manual")
        return value

    tasks = get_sprint_tasks(project, sprint_id)
    completed_tasks = [t for t in tasks if t["status"] == "completed"]

    # Collect all files touched across tasks
    all_files = set()
    all_decisions = []
    for task in completed_tasks:
        files = task.get("assigned_files", [])
        if isinstance(files, str):
            import json as _json
            files = _json.loads(files)
        all_files.update(
            sanitize_content(str(f), "manual") for f in files
        )

        # Extract decisions from agent output
        outputs = task.get("agent_outputs")
        if outputs:
            try:
                parsed = json.loads(outputs)
                all_decisions.extend(_sanitize_nested(parsed.get("decisions", [])))
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "role": sanitize_content("SprintReviewer", "manual"),
        "project": sanitize_content(project, "manual"),
        "sprint_id": sanitize_content(sprint_id, "manual"),
        "review_type": sanitize_content("cross_task_consistency", "manual"),
        "completed_tasks": [
            {
                "task_id": sanitize_content(str(t["task_id"]), "manual"),
                "task_name": sanitize_content(str(t["task_name"]), "manual"),
                "files": _sanitize_nested(t.get("assigned_files", [])),
            }
            for t in completed_tasks
        ],
        "all_files_touched": sorted(all_files),
        "decisions_made": all_decisions,
        "consistency_checks": [
            "Naming conventions consistent across tasks?",
            "API contracts match between producer and consumer tasks?",
            "Shared utilities extracted (no copy-paste)?",
            "Error handling patterns consistent?",
            "Import patterns consistent?",
        ],
    }


def _extract_external_deps(output: dict) -> str:
    """Extract external dependency mentions from output."""
    text = json.dumps(output)
    dep_keywords = ["api", "service", "external", "third-party", "dependency"]
    found = [kw for kw in dep_keywords if kw in text.lower()]
    return f"References: {', '.join(found)}" if found else "No external dependencies detected"
