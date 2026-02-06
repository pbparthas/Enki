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
