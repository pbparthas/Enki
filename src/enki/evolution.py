"""Self-Evolution module for Enki — façade re-exporting from split modules.

P2-12: This module was split into three for SRP:
- evolution_store.py  — State I/O (load/save/init, pruning, promotion)
- evolution_analytics.py — DB queries (violation/escalation patterns, triggers)
- evolution_core.py — Business logic (corrections, gate adjustments, self-awareness)

All existing imports from `enki.evolution` continue to work unchanged.
"""

# Re-export everything from split modules for backward compatibility

# --- Core: data classes, constants, correction lifecycle ---
from .evolution_core import (  # noqa: F401
    SelfCorrection,
    GateAdjustment,
    IMMUTABLE_GATES,
    create_self_correction,
    add_gate_adjustment,
    mark_correction_effective,
    approve_correction,
    reject_correction,
    get_last_review_date,
    is_review_due,
    explain_block,
    get_evolution_summary,
    get_self_awareness_response,
    get_evolution_context_for_session,
    _merge_evolution_states,
    _format_evolution_for_injection,
)

# --- Store: I/O, paths, pruning, promotion ---
from .evolution_store import (  # noqa: F401
    get_evolution_path,
    get_local_evolution_path,
    get_global_evolution_path,
    get_promotion_candidates_path,
    init_evolution_log,
    load_evolution_state,
    save_evolution_state,
    _save_evolution_to_path,
    migrate_per_project_evolution,
    promote_to_global,
    prune_local_evolution,
    prune_global_evolution,
)

# --- Analytics: DB queries, triggers, weekly review ---
from .evolution_analytics import (  # noqa: F401
    TRIGGER_THRESHOLDS,
    analyze_violation_patterns,
    analyze_escalation_patterns,
    find_rework_correlation,
    check_correction_triggers,
    run_weekly_self_review,
)
