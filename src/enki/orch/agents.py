"""agents.py — 13 agent definitions + prompt assembly from prompts/ files.

Static parts (identity, standards) in prompt files.
Dynamic part (project context) assembled at runtime.
EM is the only agent seeing full state — MUST filter what subagents see.
"""

import json
from enum import Enum
from pathlib import Path

from enki.db import ENKI_ROOT

PROMPTS_DIR = ENKI_ROOT / "prompts"


class AgentRole(str, Enum):
    PM = "pm"
    ARCHITECT = "architect"
    DBA = "dba"
    DEV = "dev"
    QA = "qa"
    UI_UX = "ui_ux"
    VALIDATOR = "validator"
    REVIEWER = "reviewer"
    INFOSEC = "infosec"
    DEVOPS = "devops"
    PERFORMANCE = "performance"
    RESEARCHER = "researcher"
    EM = "em"


# Agent metadata
AGENTS = {
    AgentRole.PM: {
        "name": "PM",
        "full_name": "Product Manager",
        "spawned_by": "enki",
        "category": "planning",
        "conditional": False,
    },
    AgentRole.ARCHITECT: {
        "name": "Architect",
        "full_name": "Architect",
        "spawned_by": "enki",
        "category": "planning",
        "conditional": False,
    },
    AgentRole.DBA: {
        "name": "DBA",
        "full_name": "Database Architect",
        "spawned_by": "enki",
        "category": "planning",
        "conditional": False,
    },
    AgentRole.DEV: {
        "name": "Dev",
        "full_name": "Developer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": False,
    },
    AgentRole.QA: {
        "name": "QA",
        "full_name": "Test Engineer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": False,
    },
    AgentRole.UI_UX: {
        "name": "UI/UX",
        "full_name": "UI/UX Designer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": True,
        "triggers": {
            "extensions": [".tsx", ".jsx", ".vue", ".css", ".scss", ".svelte"],
            "dirs": ["components/", "pages/", "views/", "styles/"],
        },
    },
    AgentRole.VALIDATOR: {
        "name": "Validator",
        "full_name": "Spec-Compliance Auditor",
        "spawned_by": "em",
        "category": "execution",
        "conditional": False,
    },
    AgentRole.REVIEWER: {
        "name": "Reviewer",
        "full_name": "Code Reviewer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": False,
    },
    AgentRole.INFOSEC: {
        "name": "InfoSec",
        "full_name": "InfoSec Reviewer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": True,
        "triggers": {
            "keywords": ["auth", "login", "password", "token", "session",
                         "encrypt", "secret", "credential", "oauth", "jwt"],
        },
    },
    AgentRole.DEVOPS: {
        "name": "DevOps",
        "full_name": "DevOps Engineer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": False,
    },
    AgentRole.PERFORMANCE: {
        "name": "Performance",
        "full_name": "Performance Engineer",
        "spawned_by": "em",
        "category": "execution",
        "conditional": True,
        "triggers": {
            "keywords": ["performance", "benchmark", "latency", "throughput",
                         "optimization", "profiling", "p99", "cache"],
        },
    },
    AgentRole.RESEARCHER: {
        "name": "Researcher",
        "full_name": "Codebase Investigator",
        "spawned_by": "em",
        "category": "execution",
        "conditional": True,
    },
    AgentRole.EM: {
        "name": "EM",
        "full_name": "Engineering Manager",
        "spawned_by": "enki",
        "category": "coordination",
        "conditional": False,
    },
}


def load_prompt(role: AgentRole) -> str:
    """Load the static prompt file for an agent.

    Returns the content of the prompt file, or a fallback if not found.
    """
    prompt_file = PROMPTS_DIR / f"{role.value}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_file}")
    return prompt_file.read_text()


def load_base_prompt() -> str:
    """Load the shared base prompt template."""
    base_file = PROMPTS_DIR / "_base.md"
    if not base_file.exists():
        raise FileNotFoundError(f"Missing base prompt: {base_file}")
    return base_file.read_text()


def load_coding_standards() -> str:
    """Load shared coding standards."""
    standards_file = PROMPTS_DIR / "_coding_standards.md"
    if not standards_file.exists():
        raise FileNotFoundError(f"Missing coding standards prompt: {standards_file}")
    return standards_file.read_text()


def assemble_prompt(
    role: AgentRole,
    task_context: dict | None = None,
    claude_md: str | None = None,
    codebase_profile: dict | None = None,
    historical_context: list[dict] | None = None,
    filtered_mail: list[dict] | None = None,
) -> str:
    """Assemble a complete prompt for an agent.

    Final prompt = _base.md + agent-specific + project context

    Args:
        role: Which agent to build the prompt for.
        task_context: Task assignment details.
        claude_md: Project CLAUDE.md content.
        codebase_profile: Researcher's Codebase Profile JSON.
        historical_context: Relevant beads from Abzu.
        filtered_mail: Agent-specific filtered mail thread.
    """
    parts = []

    # 1. Base template
    parts.append(load_base_prompt())

    # 2. Coding standards (for Dev, Reviewer, QA)
    if role in (AgentRole.DEV, AgentRole.REVIEWER, AgentRole.QA):
        standards = load_coding_standards()
        if standards:
            parts.append("\n---\n")
            parts.append(standards)

    # 3. Agent-specific prompt
    parts.append("\n---\n")
    parts.append(load_prompt(role))

    # 4. Project context (dynamic — injected at runtime)
    if claude_md:
        parts.append("\n---\n## PROJECT CONTEXT (CLAUDE.md)\n")
        parts.append(claude_md)

    if codebase_profile:
        parts.append("\n---\n## CODEBASE PROFILE\n```json\n")
        parts.append(json.dumps(codebase_profile, indent=2))
        parts.append("\n```")

    if historical_context:
        parts.append("\n---\n## HISTORICAL CONTEXT\n")
        for bead in historical_context:
            parts.append(
                f"- [{bead.get('category', 'unknown')}] "
                f"{bead.get('content', '')[:200]}"
            )

    if task_context:
        parts.append("\n---\n## TASK ASSIGNMENT\n")
        parts.append(json.dumps(task_context, indent=2))

    if filtered_mail:
        parts.append("\n---\n## RELEVANT MAIL\n")
        for msg in filtered_mail[-10:]:  # Last 10 messages
            parts.append(
                f"**{msg.get('from_agent', '?')} → {msg.get('to_agent', '?')}**: "
                f"{msg.get('body', '')[:300]}"
            )

    return "\n".join(parts)


def should_spawn(role: AgentRole, context: dict) -> bool:
    """Check if a conditional agent should be spawned.

    Args:
        role: Agent role to check.
        context: Dict with 'files', 'keywords', 'spec_text', 'codebase_profile'.
    """
    agent = AGENTS.get(role)
    if not agent:
        return False

    if not agent.get("conditional", False):
        return True  # Always spawn non-conditional agents

    triggers = agent.get("triggers", {})

    # Check file extensions
    if "extensions" in triggers:
        files = context.get("files", [])
        for f in files:
            for ext in triggers["extensions"]:
                if f.endswith(ext):
                    return True

    # Check directory patterns
    if "dirs" in triggers:
        files = context.get("files", [])
        for f in files:
            for d in triggers["dirs"]:
                if d in f:
                    return True

    # Check keywords
    if "keywords" in triggers:
        text = (
            " ".join(context.get("keywords", []))
            + " "
            + context.get("spec_text", "")
        ).lower()
        for kw in triggers["keywords"]:
            if kw in text:
                return True

    # Researcher: spawn on-demand (EM decides)
    if role == AgentRole.RESEARCHER:
        return context.get("researcher_needed", False)

    return False


def get_blind_wall_filter(role: AgentRole) -> dict:
    """Get the context filtering rules for an agent.

    EM is the only agent seeing full state. This defines what
    each subagent is allowed to see.
    """
    filters = {
        AgentRole.DEV: {
            "exclude": ["qa_output", "test_results", "test_code"],
        },
        AgentRole.QA: {
            "exclude": ["dev_output", "implementation_details", "source_code"],
        },
        AgentRole.VALIDATOR: {
            "exclude": ["agent_reasoning", "mail_threads"],
        },
        AgentRole.REVIEWER: {
            "exclude": ["test_details", "qa_reasoning"],
        },
    }
    return filters.get(role, {"exclude": []})
