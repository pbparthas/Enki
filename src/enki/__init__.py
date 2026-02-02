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
]
