"""Shared enforcement types — P2-16.

Defines the contract between enforcement.py (gate checks) and
ereshkigal.py (pattern interception). Both modules import from here.

GateResult and InterceptionResult remain in their original modules.
Both can convert to EnforcementDecision via to_decision().
"""

from dataclasses import dataclass
from typing import Optional


# P3-04: Tool name constants — canonical tool names used across enforcement
class Tools:
    """Canonical tool names for enforcement checks."""
    EDIT = "Edit"
    WRITE = "Write"
    MULTI_EDIT = "MultiEdit"
    BASH = "Bash"
    TASK = "Task"
    READ = "Read"
    GLOB = "Glob"
    GREP = "Grep"
    SKILL = "Skill"


# Tools that modify files (used by gate checks)
FILE_MODIFY_TOOLS = {Tools.EDIT, Tools.WRITE, Tools.MULTI_EDIT, Tools.BASH}


@dataclass
class EnforcementDecision:
    """Unified decision type for audit/logging across gates and interceptions.

    Both GateResult and InterceptionResult can produce this via to_decision().
    Use this type when you need a source-agnostic enforcement decision.
    """
    allowed: bool
    source: str  # "gate" or "interception"
    gate: Optional[str] = None  # Gate name (for gate decisions)
    category: Optional[str] = None  # Pattern category (for interception decisions)
    pattern: Optional[str] = None  # Matched pattern (for interception decisions)
    reason: Optional[str] = None
    decision_id: Optional[str] = None  # For tracking/audit
