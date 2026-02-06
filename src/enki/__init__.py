"""Enki - Second brain for software engineering."""

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Lazy-loading setup
# ---------------------------------------------------------------------------
# Instead of eagerly importing all 18+ submodules (which transitively pulls in
# numpy, sentence-transformers, etc.), we use Python's module-level __getattr__
# to import on first access.  This keeps `import enki` near-instant.
#
# The _LAZY_SUBMODULES dict maps submodule attribute names to their dotted path
# so that `enki.beads`, `enki.db`, etc. still work.
#
# The _LAZY_ATTRS dict maps every public name to (dotted_module, real_name) so
# that `from enki import create_bead` still works -- it just triggers the
# import of enki.beads at that point rather than at package init time.
# ---------------------------------------------------------------------------

_LAZY_SUBMODULES: dict[str, str] = {
    "beads": "enki.beads",
    "context": "enki.context",
    "db": "enki.db",
    "embeddings": "enki.embeddings",
    "enforcement": "enki.enforcement",
    "ereshkigal": "enki.ereshkigal",
    "evolution": "enki.evolution",
    "migration": "enki.migration",
    "mcp_server": "enki.mcp_server",
    "onboarding": "enki.onboarding",
    "orchestrator": "enki.orchestrator",
    "persona": "enki.persona",
    "pm": "enki.pm",
    "reflector": "enki.reflector",
    "retention": "enki.retention",
    "search": "enki.search",
    "session": "enki.session",
    "simplifier": "enki.simplifier",
    "skills": "enki.skills",
    "style_learning": "enki.style_learning",
    "summarization": "enki.summarization",
    "worktree": "enki.worktree",
}

# Maps public name -> (module_path, attribute_name_in_that_module)
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    # Database
    "get_db": ("enki.db", "get_db"),
    "init_db": ("enki.db", "init_db"),
    # Beads
    "create_bead": ("enki.beads", "create_bead"),
    "get_bead": ("enki.beads", "get_bead"),
    "update_bead": ("enki.beads", "update_bead"),
    "delete_bead": ("enki.beads", "delete_bead"),
    # Search
    "search": ("enki.search", "search"),
    # Retention
    "calculate_weight": ("enki.retention", "calculate_weight"),
    # Session
    "start_session": ("enki.session", "start_session"),
    "get_session": ("enki.session", "get_session"),
    "get_phase": ("enki.session", "get_phase"),
    "set_phase": ("enki.session", "set_phase"),
    "get_tier": ("enki.session", "get_tier"),
    "set_tier": ("enki.session", "set_tier"),
    "get_goal": ("enki.session", "get_goal"),
    "set_goal": ("enki.session", "set_goal"),
    # Enforcement
    "check_all_gates": ("enki.enforcement", "check_all_gates"),
    "detect_tier": ("enki.enforcement", "detect_tier"),
    # PM
    "generate_perspectives": ("enki.pm", "generate_perspectives"),
    "check_perspectives_complete": ("enki.pm", "check_perspectives_complete"),
    "create_spec": ("enki.pm", "create_spec"),
    "get_spec": ("enki.pm", "get_spec"),
    "list_specs": ("enki.pm", "list_specs"),
    "approve_spec": ("enki.pm", "approve_spec"),
    "is_spec_approved": ("enki.pm", "is_spec_approved"),
    "decompose_spec": ("enki.pm", "decompose_spec"),
    "Task": ("enki.pm", "Task"),
    "TaskGraph": ("enki.pm", "TaskGraph"),
    "save_task_graph": ("enki.pm", "save_task_graph"),
    "load_task_graph": ("enki.pm", "load_task_graph"),
    "get_orchestration_status": ("enki.pm", "get_orchestration_status"),
    # Orchestrator
    "Bug": ("enki.orchestrator", "Bug"),
    "Orchestration": ("enki.orchestrator", "Orchestration"),
    "AGENTS": ("enki.orchestrator", "AGENTS"),
    "start_orchestration": ("enki.orchestrator", "start_orchestration"),
    "load_orchestration": ("enki.orchestrator", "load_orchestration"),
    "save_orchestration": ("enki.orchestrator", "save_orchestration"),
    "start_task": ("enki.orchestrator", "start_task"),
    "complete_task": ("enki.orchestrator", "complete_task"),
    "fail_task": ("enki.orchestrator", "fail_task"),
    "file_bug": ("enki.orchestrator", "file_bug"),
    "assign_bug": ("enki.orchestrator", "assign_bug"),
    "close_bug": ("enki.orchestrator", "close_bug"),
    "reopen_bug": ("enki.orchestrator", "reopen_bug"),
    "get_open_bugs": ("enki.orchestrator", "get_open_bugs"),
    "escalate_to_hitl": ("enki.orchestrator", "escalate_to_hitl"),
    "resolve_hitl": ("enki.orchestrator", "resolve_hitl"),
    "check_hitl_required": ("enki.orchestrator", "check_hitl_required"),
    "get_full_orchestration_status": ("enki.orchestrator", "get_full_orchestration_status"),
    "get_next_action": ("enki.orchestrator", "get_next_action"),
    "generate_agent_prompt": ("enki.orchestrator", "generate_agent_prompt"),
    "get_spawn_task_call": ("enki.orchestrator", "get_spawn_task_call"),
    "spawn_agent_for_task": ("enki.orchestrator", "spawn_agent_for_task"),
    "get_parallel_spawn_calls": ("enki.orchestrator", "get_parallel_spawn_calls"),
    # Persona
    "PersonaContext": ("enki.persona", "PersonaContext"),
    "get_persona_context": ("enki.persona", "get_persona_context"),
    "build_session_start_injection": ("enki.persona", "build_session_start_injection"),
    "build_error_context_injection": ("enki.persona", "build_error_context_injection"),
    "build_decision_context": ("enki.persona", "build_decision_context"),
    "get_enki_greeting": ("enki.persona", "get_enki_greeting"),
    "generate_session_summary": ("enki.persona", "generate_session_summary"),
    "extract_session_learnings": ("enki.persona", "extract_session_learnings"),
    # Evolution
    "SelfCorrection": ("enki.evolution", "SelfCorrection"),
    "GateAdjustment": ("enki.evolution", "GateAdjustment"),
    "init_evolution_log": ("enki.evolution", "init_evolution_log"),
    "load_evolution_state": ("enki.evolution", "load_evolution_state"),
    "save_evolution_state": ("enki.evolution", "save_evolution_state"),
    "analyze_violation_patterns": ("enki.evolution", "analyze_violation_patterns"),
    "analyze_escalation_patterns": ("enki.evolution", "analyze_escalation_patterns"),
    "check_correction_triggers": ("enki.evolution", "check_correction_triggers"),
    "create_self_correction": ("enki.evolution", "create_self_correction"),
    "add_gate_adjustment": ("enki.evolution", "add_gate_adjustment"),
    "run_weekly_self_review": ("enki.evolution", "run_weekly_self_review"),
    "is_review_due": ("enki.evolution", "is_review_due"),
    "explain_block": ("enki.evolution", "explain_block"),
    "get_evolution_summary": ("enki.evolution", "get_evolution_summary"),
    "get_self_awareness_response": ("enki.evolution", "get_self_awareness_response"),
    "get_local_evolution_path": ("enki.evolution", "get_local_evolution_path"),
    "get_global_evolution_path": ("enki.evolution", "get_global_evolution_path"),
    "migrate_per_project_evolution": ("enki.evolution", "migrate_per_project_evolution"),
    "promote_to_global": ("enki.evolution", "promote_to_global"),
    "get_evolution_context_for_session": ("enki.evolution", "get_evolution_context_for_session"),
    "prune_local_evolution": ("enki.evolution", "prune_local_evolution"),
    "prune_global_evolution": ("enki.evolution", "prune_global_evolution"),
    # Ereshkigal
    "InterceptionResult": ("enki.ereshkigal", "InterceptionResult"),
    "init_patterns": ("enki.ereshkigal", "init_patterns"),
    "load_patterns": ("enki.ereshkigal", "load_patterns"),
    "save_patterns": ("enki.ereshkigal", "save_patterns"),
    "add_pattern": ("enki.ereshkigal", "add_pattern"),
    "remove_pattern": ("enki.ereshkigal", "remove_pattern"),
    "get_pattern_categories": ("enki.ereshkigal", "get_pattern_categories"),
    "intercept": ("enki.ereshkigal", "intercept"),
    "would_block": ("enki.ereshkigal", "would_block"),
    "log_attempt": ("enki.ereshkigal", "log_attempt"),
    "mark_false_positive": ("enki.ereshkigal", "mark_false_positive"),
    "mark_legitimate": ("enki.ereshkigal", "mark_legitimate"),
    "get_interception_stats": ("enki.ereshkigal", "get_interception_stats"),
    "get_recent_interceptions": ("enki.ereshkigal", "get_recent_interceptions"),
    "generate_weekly_report": ("enki.ereshkigal", "generate_weekly_report"),
    "get_last_review_date": ("enki.ereshkigal", "get_last_review_date"),
    "save_review_date": ("enki.ereshkigal", "save_review_date"),
    "is_review_overdue": ("enki.ereshkigal", "is_review_overdue"),
    "get_review_reminder": ("enki.ereshkigal", "get_review_reminder"),
    "find_evasions_with_bugs": ("enki.ereshkigal", "find_evasions_with_bugs"),
    "generate_fresh_claude_prompt": ("enki.ereshkigal", "generate_fresh_claude_prompt"),
    "generate_review_checklist": ("enki.ereshkigal", "generate_review_checklist"),
    "complete_review": ("enki.ereshkigal", "complete_review"),
    "get_report_summary": ("enki.ereshkigal", "get_report_summary"),
    # Migration
    "MigrationResult": ("enki.migration", "MigrationResult"),
    "migrate_to_enki": ("enki.migration", "migrate_to_enki"),
    "validate_migration": ("enki.migration", "validate_migration"),
    "rollback_migration": ("enki.migration", "rollback_migration"),
    # Style learning
    "StylePattern": ("enki.style_learning", "StylePattern"),
    "analyze_session_patterns": ("enki.style_learning", "analyze_session_patterns"),
    "learn_from_session": ("enki.style_learning", "learn_from_session"),
    "save_style_patterns": ("enki.style_learning", "save_style_patterns"),
    "get_style_summary": ("enki.style_learning", "get_style_summary"),
    # Onboarding
    "ExtractedKnowledge": ("enki.onboarding", "ExtractedKnowledge"),
    "onboard_project": ("enki.onboarding", "onboard_project"),
    "get_onboarding_preview": ("enki.onboarding", "get_onboarding_preview"),
    "get_onboarding_status": ("enki.onboarding", "get_onboarding_status"),
    # Skills
    "SKILLS": ("enki.skills", "SKILLS"),
    "SkillInvocation": ("enki.skills", "SkillInvocation"),
    "get_skill_for_agent": ("enki.skills", "get_skill_for_agent"),
    "get_skill_invocation": ("enki.skills", "get_skill_invocation"),
    "get_skill_prompt": ("enki.skills", "get_skill_prompt"),
    "list_available_skills": ("enki.skills", "list_available_skills"),
    "get_agent_skill_prompt": ("enki.skills", "get_agent_skill_prompt"),
    "enhance_agent_prompt_with_skill": ("enki.skills", "enhance_agent_prompt_with_skill"),
    # Summarization
    "SummarizationCandidate": ("enki.summarization", "SummarizationCandidate"),
    "find_summarization_candidates": ("enki.summarization", "find_summarization_candidates"),
    "generate_summary": ("enki.summarization", "generate_summary"),
    "summarize_bead": ("enki.summarization", "summarize_bead"),
    "run_session_summarization": ("enki.summarization", "run_session_summarization"),
    "get_summarization_preview": ("enki.summarization", "get_summarization_preview"),
    "get_summarization_stats": ("enki.summarization", "get_summarization_stats"),
    # Worktree
    "Worktree": ("enki.worktree", "Worktree"),
    "create_worktree": ("enki.worktree", "create_worktree"),
    "list_worktrees": ("enki.worktree", "list_worktrees"),
    "get_worktree": ("enki.worktree", "get_worktree"),
    "remove_worktree": ("enki.worktree", "remove_worktree"),
    "merge_worktree": ("enki.worktree", "merge_worktree"),
    "exec_in_worktree": ("enki.worktree", "exec_in_worktree"),
    "is_in_worktree": ("enki.worktree", "is_in_worktree"),
    "get_worktree_state": ("enki.worktree", "get_worktree_state"),
    "get_worktree_root": ("enki.worktree", "get_worktree_root"),
    # Context
    "ContextTier": ("enki.context", "ContextTier"),
    "LoadedContext": ("enki.context", "LoadedContext"),
    "detect_context_tier": ("enki.context", "detect_tier"),
    "load_context": ("enki.context", "load_context"),
    "format_context_for_injection": ("enki.context", "format_context_for_injection"),
    "preview_context": ("enki.context", "preview_context"),
    "set_default_tier": ("enki.context", "set_default_tier"),
    "get_context_config": ("enki.context", "get_context_config"),
    "save_context_config": ("enki.context", "save_context_config"),
    # Simplifier
    "SimplificationResult": ("enki.simplifier", "SimplificationResult"),
    "generate_simplifier_prompt": ("enki.simplifier", "generate_simplifier_prompt"),
    "run_simplification": ("enki.simplifier", "run_simplification"),
    "parse_simplification_output": ("enki.simplifier", "parse_simplification_output"),
    "get_modified_files": ("enki.simplifier", "get_modified_files"),
    "SIMPLIFIER_PROMPT": ("enki.simplifier", "SIMPLIFIER_PROMPT"),
    # Reflector
    "ExecutionTrace": ("enki.reflector", "ExecutionTrace"),
    "Reflection": ("enki.reflector", "Reflection"),
    "Skill": ("enki.reflector", "Skill"),
    "gather_execution_trace": ("enki.reflector", "gather_execution_trace"),
    "reflect_on_session": ("enki.reflector", "reflect_on_session"),
    "distill_reflections": ("enki.reflector", "distill_reflections"),
    "store_skills": ("enki.reflector", "store_skills"),
    "close_feedback_loop": ("enki.reflector", "close_feedback_loop"),
    "analyze_cross_session_patterns": ("enki.reflector", "analyze_cross_session_patterns"),
}


def __getattr__(name: str):
    """Lazy-load submodules and public attributes on first access."""
    import importlib

    # Submodule access: enki.beads, enki.db, etc.
    if name in _LAZY_SUBMODULES:
        module = importlib.import_module(_LAZY_SUBMODULES[name])
        globals()[name] = module
        return module

    # Individual attribute access: from enki import create_bead, etc.
    if name in _LAZY_ATTRS:
        module_path, attr_name = _LAZY_ATTRS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        globals()[name] = attr
        return attr

    raise AttributeError(f"module 'enki' has no attribute {name!r}")


def __dir__():
    """Include lazy attributes in dir() for discoverability."""
    normal = list(globals().keys())
    return normal + list(_LAZY_SUBMODULES.keys()) + list(_LAZY_ATTRS.keys())


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
    # Agent spawning
    "generate_agent_prompt",
    "get_spawn_task_call",
    "spawn_agent_for_task",
    "get_parallel_spawn_calls",
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
    # Two-tier evolution
    "get_local_evolution_path",
    "get_global_evolution_path",
    "migrate_per_project_evolution",
    "promote_to_global",
    "get_evolution_context_for_session",
    "prune_local_evolution",
    "prune_global_evolution",
    # Ereshkigal
    "InterceptionResult",
    "init_patterns",
    "load_patterns",
    "save_patterns",
    "add_pattern",
    "remove_pattern",
    "get_pattern_categories",
    "intercept",
    "would_block",
    "log_attempt",
    "mark_false_positive",
    "mark_legitimate",
    "get_interception_stats",
    "get_recent_interceptions",
    "generate_weekly_report",
    # Phase 8: External Pattern Evolution
    "get_last_review_date",
    "save_review_date",
    "is_review_overdue",
    "get_review_reminder",
    "find_evasions_with_bugs",
    "generate_fresh_claude_prompt",
    "generate_review_checklist",
    "complete_review",
    "get_report_summary",
    # Phase 0: Migration
    "MigrationResult",
    "migrate_to_enki",
    "validate_migration",
    "rollback_migration",
    # Working style learning
    "StylePattern",
    "analyze_session_patterns",
    "learn_from_session",
    "save_style_patterns",
    "get_style_summary",
    # Project onboarding
    "ExtractedKnowledge",
    "onboard_project",
    "get_onboarding_preview",
    "get_onboarding_status",
    # Skills integration
    "SKILLS",
    "SkillInvocation",
    "get_skill_for_agent",
    "get_skill_invocation",
    "get_skill_prompt",
    "list_available_skills",
    "get_agent_skill_prompt",
    "enhance_agent_prompt_with_skill",
    # Session summarization
    "SummarizationCandidate",
    "find_summarization_candidates",
    "generate_summary",
    "summarize_bead",
    "run_session_summarization",
    "get_summarization_preview",
    "get_summarization_stats",
    # Worktree management
    "Worktree",
    "create_worktree",
    "list_worktrees",
    "get_worktree",
    "remove_worktree",
    "merge_worktree",
    "exec_in_worktree",
    "is_in_worktree",
    "get_worktree_state",
    "get_worktree_root",
    # Adaptive context loading
    "ContextTier",
    "LoadedContext",
    "detect_context_tier",
    "load_context",
    "format_context_for_injection",
    "preview_context",
    "set_default_tier",
    "get_context_config",
    "save_context_config",
    # Simplifier agent
    "SimplificationResult",
    "generate_simplifier_prompt",
    "run_simplification",
    "parse_simplification_output",
    "get_modified_files",
    "SIMPLIFIER_PROMPT",
    # Reflector
    "ExecutionTrace",
    "Reflection",
    "Skill",
    "gather_execution_trace",
    "reflect_on_session",
    "distill_reflections",
    "store_skills",
    "close_feedback_loop",
    "analyze_cross_session_patterns",
]
