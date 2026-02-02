"""Enki - Second brain for software engineering."""

__version__ = "0.1.0"

from .db import get_db, init_db
from .beads import create_bead, get_bead, update_bead, delete_bead
from .search import search
from .retention import calculate_weight
from .session import (
    start_session, get_session, get_phase, set_phase,
    get_tier, set_tier, get_goal, set_goal,
)
from .enforcement import check_all_gates, detect_tier
from .pm import (
    generate_perspectives, check_perspectives_complete,
    create_spec, get_spec, list_specs, approve_spec, is_spec_approved,
    decompose_spec, Task, TaskGraph,
    save_task_graph, load_task_graph, get_orchestration_status,
)
from .orchestrator import (
    Bug, Orchestration, AGENTS,
    start_orchestration, load_orchestration, save_orchestration,
    start_task, complete_task, fail_task,
    file_bug, assign_bug, close_bug, reopen_bug, get_open_bugs,
    escalate_to_hitl, resolve_hitl, check_hitl_required,
    get_full_orchestration_status, get_next_action,
)
from .persona import (
    PersonaContext,
    get_persona_context,
    build_session_start_injection,
    build_error_context_injection,
    build_decision_context,
    get_enki_greeting,
    generate_session_summary,
    extract_session_learnings,
)
from .evolution import (
    SelfCorrection,
    GateAdjustment,
    init_evolution_log,
    load_evolution_state,
    save_evolution_state,
    analyze_violation_patterns,
    analyze_escalation_patterns,
    check_correction_triggers,
    create_self_correction,
    add_gate_adjustment,
    run_weekly_self_review,
    is_review_due,
    explain_block,
    get_evolution_summary,
    get_self_awareness_response,
)

__all__ = [
    # Database
    "get_db",
    "init_db",
    # Beads
    "create_bead",
    "get_bead",
    "update_bead",
    "delete_bead",
    # Search
    "search",
    # Retention
    "calculate_weight",
    # Session
    "start_session",
    "get_session",
    "get_phase",
    "set_phase",
    "get_tier",
    "set_tier",
    "get_goal",
    "set_goal",
    # Enforcement
    "check_all_gates",
    "detect_tier",
    # PM
    "generate_perspectives",
    "check_perspectives_complete",
    "create_spec",
    "get_spec",
    "list_specs",
    "approve_spec",
    "is_spec_approved",
    "decompose_spec",
    "Task",
    "TaskGraph",
    "save_task_graph",
    "load_task_graph",
    "get_orchestration_status",
    # Orchestrator
    "Bug",
    "Orchestration",
    "AGENTS",
    "start_orchestration",
    "load_orchestration",
    "save_orchestration",
    "start_task",
    "complete_task",
    "fail_task",
    "file_bug",
    "assign_bug",
    "close_bug",
    "reopen_bug",
    "get_open_bugs",
    "escalate_to_hitl",
    "resolve_hitl",
    "check_hitl_required",
    "get_full_orchestration_status",
    "get_next_action",
    # Persona
    "PersonaContext",
    "get_persona_context",
    "build_session_start_injection",
    "build_error_context_injection",
    "build_decision_context",
    "get_enki_greeting",
    "generate_session_summary",
    "extract_session_learnings",
    # Evolution
    "SelfCorrection",
    "GateAdjustment",
    "init_evolution_log",
    "load_evolution_state",
    "save_evolution_state",
    "analyze_violation_patterns",
    "analyze_escalation_patterns",
    "check_correction_triggers",
    "create_self_correction",
    "add_gate_adjustment",
    "run_weekly_self_review",
    "is_review_due",
    "explain_block",
    "get_evolution_summary",
    "get_self_awareness_response",
]
