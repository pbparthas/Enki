"""Agent configuration — separated from orchestrator logic (P2-09).

Agent definitions and worker-validator mappings live here.
Orchestrator imports from this module.
"""

# Agent definitions with their roles and allowed tools
AGENTS = {
    "Architect": {
        "role": "Design before implementation",
        "tier": "CRITICAL",
        "tools": ["Read", "Glob", "Grep", "Write"],
        "writes_to": ["docs/", "specs/"],
    },
    "QA": {
        "role": "Write tests FIRST (TDD), execute tests",
        "tier": "CRITICAL",
        "tools": ["Read", "Write", "Bash"],
        "writes_to": ["tests/"],
    },
    "Validator-Tests": {
        "role": "Verify QA tests match spec",
        "tier": "CRITICAL",
        "tools": ["Read", "Grep"],
        "writes_to": [],
    },
    "Dev": {
        "role": "Implement to pass tests (SOLID)",
        "tier": "CRITICAL",
        "tools": ["Read", "Edit", "Write"],
        "writes_to": ["src/", "lib/"],
    },
    "Validator-Code": {
        "role": "Verify implementation correctness",
        "tier": "CRITICAL",
        "tools": ["Read", "Grep", "Bash"],
        "writes_to": [],
    },
    "Reviewer": {
        "role": "Code review via Prism",
        "tier": "STANDARD",
        "tools": ["Skill"],
        "skill": "/review",
    },
    "DBA": {
        "role": "Database changes",
        "tier": "CONDITIONAL",
        "tools": ["Read", "Write", "Bash"],
        "writes_to": ["migrations/", "sql/"],
    },
    "Security": {
        "role": "Security review",
        "tier": "STANDARD",
        "tools": ["Skill"],
        "skill": "/security-review",
    },
    "Docs": {
        "role": "Documentation updates",
        "tier": "STANDARD",
        "tools": ["Read", "Write"],
        "writes_to": ["docs/", "README"],
    },
    "Simplifier": {
        "role": "Reduce complexity without changing behavior",
        "tier": "STANDARD",
        "tools": ["Read", "Edit", "Bash"],
        "writes_to": ["src/", "lib/"],
    },
    "Validator-Security": {
        "role": "Security code review using Sentinel skill",
        "tier": "STANDARD",
        "tools": ["Read", "Glob", "Grep"],
        "writes_to": [],  # Read-only reviewer
        "skill": "/sentinel-security",
    },
    "DevOps": {
        "role": "CI/CD, deployment, infrastructure",
        "tier": "CONDITIONAL",
        "tools": ["Read", "Edit", "Write", "Bash"],
        "writes_to": [".github/", ".gitlab-ci/", "Dockerfile", "docker-compose", "deploy/", "infra/"],
    },
    "UI-UX": {
        "role": "Frontend design, accessibility, responsive UI",
        "tier": "CONDITIONAL",
        "tools": ["Read", "Edit", "Write", "Bash"],
        "writes_to": ["src/components/", "src/pages/", "src/views/", "src/ui/", "src/styles/", "styles/", "components/", "public/"],
        "skill": "/frontend-design",
    },
    "Performance": {
        "role": "Profiling, optimization, benchmarking",
        "tier": "STANDARD",
        "tools": ["Read", "Glob", "Grep", "Skill"],
        "writes_to": [],  # Read-only analysis
        "skill": "/performance-analyzer",
    },
    # Sentinel agents (Hardening Spec v2, Step 7 — decomposed from monolithic validator)
    "Sentinel-Bugs": {
        "role": "Detect potential bugs via static analysis patterns",
        "tier": "STANDARD",
        "tools": ["Read", "Grep", "Glob"],
        "writes_to": [],
        "validation_tier": 2,  # LLM review, advisory only
    },
    "Sentinel-Maintainability": {
        "role": "Flag complexity, duplication, coupling issues",
        "tier": "STANDARD",
        "tools": ["Read", "Grep", "Glob"],
        "writes_to": [],
        "validation_tier": 2,
    },
    "Sentinel-TypeSafety": {
        "role": "Verify type annotations and type correctness",
        "tier": "STANDARD",
        "tools": ["Read", "Grep", "Glob"],
        "writes_to": [],
        "validation_tier": 2,
    },
    "Sentinel-Simplicity": {
        "role": "Identify over-engineering and unnecessary abstraction",
        "tier": "STANDARD",
        "tools": ["Read", "Grep", "Glob"],
        "writes_to": [],
        "validation_tier": 2,
    },
    "Sentinel-Governance": {
        "role": "Verify enforcement gates, hook integrity, config compliance",
        "tier": "STANDARD",
        "tools": ["Read", "Grep", "Glob"],
        "writes_to": [],
        "validation_tier": 2,
    },
}

# Map workers to their validators (two-stage where applicable)
WORKER_VALIDATORS = {
    "QA": ["Validator-Tests"],
    "Dev": ["Validator-Tests", "Validator-Code"],  # Two-stage: spec compliance, then code quality
    "Architect": [],  # Design review is manual
    "DBA": ["Validator-Code"],
    "Docs": [],  # Doc review is manual
    "Security": [],
    "Reviewer": [],
    "DevOps": ["Validator-Code", "Validator-Security"],  # Security validates CI/CD changes
    "UI-UX": ["Validator-Code"],  # Code validates frontend changes
    "Performance": [],  # Analysis-only, no validation needed
}


# === Validation Hierarchy (Hardening Spec v2, Step 7) ===
#
# Tier 1 (deterministic, mandatory): shell commands — tests, linters, type-checkers.
#         MUST pass. Gate completion. No override except HITL (Tier 3).
# Tier 2 (LLM review, advisory): sentinel agents. Findings surfaced but don't gate.
#         No code path allows Tier 2 to override a Tier 1 failure.
# Tier 3 (human override): only a human can override Tier 1 failure via HITL.

VALIDATION_TIERS = {
    1: {
        "name": "deterministic",
        "description": "Shell commands, test suites, linters, type-checkers",
        "mandatory": True,   # MUST pass to proceed
        "override": "hitl",  # Only human can override
    },
    2: {
        "name": "llm_review",
        "description": "Agent-based review — sentinel agents, findings advisory only",
        "mandatory": False,  # Findings surfaced but don't gate
        "override": None,    # No override needed since not mandatory
    },
    3: {
        "name": "human",
        "description": "Human-in-the-loop — only path to override Tier 1 failure",
        "mandatory": True,
        "override": None,    # Human IS the override
    },
}

# Which validators are deterministic (Tier 1) vs LLM advisory (Tier 2)
VALIDATOR_TIERS = {
    # Tier 1: Deterministic — these GATE completion
    "Validator-Tests": 1,
    "Validator-Code": 1,
    "Validator-Security": 1,
    # Tier 2: LLM advisory — findings surfaced, don't gate
    "Sentinel-Bugs": 2,
    "Sentinel-Maintainability": 2,
    "Sentinel-TypeSafety": 2,
    "Sentinel-Simplicity": 2,
    "Sentinel-Governance": 2,
    # Tier 1: General review has deterministic checks
    "Reviewer": 2,
    "Security": 2,
}
