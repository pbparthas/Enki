"""Skill Integration for Enki.

Integrates Prism skills (/review, /security-review) with orchestration.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Skill definitions with their parameters
SKILLS = {
    "review": {
        "name": "review",
        "description": "Prism Code Review - deterministic rules + static analysis + optional LLM semantic review",
        "agent": "Reviewer",
        "accepts_path": True,
        "accepts_options": ["--strict", "--semantic", "--files"],
    },
    "security-review": {
        "name": "security-review",
        "description": "Security audit for OWASP Top 10, secrets, injection, auth issues",
        "agent": "Security",
        "accepts_path": True,
        "accepts_options": ["--strict", "--owasp", "--secrets"],
    },
    "test-generator": {
        "name": "test-generator",
        "description": "Generate comprehensive tests (unit, integration, E2E) with mocks and edge cases",
        "agent": "QA",
        "accepts_path": True,
        "accepts_options": ["--unit", "--integration", "--e2e"],
    },
    "doc-generator": {
        "name": "doc-generator",
        "description": "Auto-generate documentation - JSDoc, docstrings, README, changelog",
        "agent": "Docs",
        "accepts_path": True,
        "accepts_options": ["--readme", "--changelog", "--inline"],
    },
    "architecture-validator": {
        "name": "architecture-validator",
        "description": "Validates code architecture against SOLID principles, layer boundaries",
        "agent": "Architect",
        "accepts_path": True,
        "accepts_options": ["--solid", "--layers", "--deps"],
    },
    "performance-analyzer": {
        "name": "performance-analyzer",
        "description": "Identifies performance anti-patterns, N+1 queries, memory leaks, and algorithmic inefficiencies",
        "agent": "Performance",
        "accepts_path": True,
        "accepts_options": ["--hotspots", "--memory", "--queries"],
    },
    "frontend-design": {
        "name": "frontend-design",
        "description": "Create distinctive, production-grade frontend interfaces with high design quality",
        "agent": "UI-UX",
        "accepts_path": True,
        "accepts_options": ["--a11y", "--responsive", "--components"],
    },
}


@dataclass
class SkillInvocation:
    """A skill invocation for the Skill tool."""
    skill: str
    args: Optional[str] = None


def get_skill_for_agent(agent: str) -> Optional[dict]:
    """Get the skill definition for an agent.

    Args:
        agent: Agent name (e.g., "Reviewer", "Security")

    Returns:
        Skill definition dict or None if agent has no skill
    """
    for skill_name, skill_info in SKILLS.items():
        if skill_info.get("agent") == agent:
            return skill_info
    return None


def get_skill_invocation(
    skill_name: str,
    target_path: Optional[str] = None,
    options: Optional[list[str]] = None,
) -> SkillInvocation:
    """Get the Skill tool invocation parameters.

    Args:
        skill_name: Name of the skill (e.g., "review", "security-review")
        target_path: Optional path to analyze
        options: Optional list of options to pass

    Returns:
        SkillInvocation with tool parameters
    """
    if skill_name not in SKILLS:
        raise ValueError(f"Unknown skill: {skill_name}")

    args_parts = []

    if target_path:
        args_parts.append(target_path)

    if options:
        args_parts.extend(options)

    return SkillInvocation(
        skill=skill_name,
        args=" ".join(args_parts) if args_parts else None,
    )


def get_skill_prompt(
    skill_name: str,
    target_files: Optional[list[str]] = None,
    context: Optional[str] = None,
) -> str:
    """Generate a prompt for invoking a skill.

    Args:
        skill_name: Name of the skill
        target_files: Files to analyze
        context: Additional context

    Returns:
        Prompt string instructing Claude to use the skill
    """
    if skill_name not in SKILLS:
        raise ValueError(f"Unknown skill: {skill_name}")

    skill = SKILLS[skill_name]

    prompt_parts = [
        f"# Use /{skill_name} Skill",
        "",
        f"Invoke the `/{skill_name}` skill to perform: {skill['description']}",
        "",
    ]

    if target_files:
        prompt_parts.append("## Target Files")
        for f in target_files:
            prompt_parts.append(f"- {f}")
        prompt_parts.append("")

    if context:
        prompt_parts.append("## Context")
        prompt_parts.append(context)
        prompt_parts.append("")

    prompt_parts.extend([
        "## Instructions",
        f"1. Use the Skill tool with skill=\"{skill_name}\"",
    ])

    if target_files:
        prompt_parts.append(f"2. Pass the files as args: \"{' '.join(target_files)}\"")

    prompt_parts.extend([
        "",
        "## Expected Output",
        "Report the findings from the skill execution.",
    ])

    return "\n".join(prompt_parts)


def list_available_skills() -> list[dict]:
    """List all available skills.

    Returns:
        List of skill definitions
    """
    return [
        {
            "name": name,
            "description": info["description"],
            "agent": info["agent"],
            "options": info.get("accepts_options", []),
        }
        for name, info in SKILLS.items()
    ]


def get_agent_skill_prompt(
    agent: str,
    target_files: Optional[list[str]] = None,
    task_description: Optional[str] = None,
) -> Optional[str]:
    """Generate a skill invocation prompt for an agent.

    Args:
        agent: Agent name
        target_files: Files to analyze
        task_description: Task description for context

    Returns:
        Prompt for skill invocation or None if agent has no skill
    """
    skill = get_skill_for_agent(agent)
    if not skill:
        return None

    return get_skill_prompt(
        skill_name=skill["name"],
        target_files=target_files,
        context=task_description,
    )


def enhance_agent_prompt_with_skill(
    base_prompt: str,
    agent: str,
    target_files: Optional[list[str]] = None,
) -> str:
    """Enhance an agent prompt with skill invocation instructions.

    Args:
        base_prompt: Original agent prompt
        agent: Agent name
        target_files: Files to analyze

    Returns:
        Enhanced prompt with skill instructions
    """
    skill = get_skill_for_agent(agent)
    if not skill:
        return base_prompt

    skill_section = [
        "",
        f"## Skill Integration: /{skill['name']}",
        "",
        f"This task uses the `/{skill['name']}` skill.",
        "",
        "To invoke the skill, use the Skill tool:",
        "```",
        f"skill: \"{skill['name']}\"",
    ]

    if target_files:
        skill_section.append(f"args: \"{' '.join(target_files)}\"")

    skill_section.extend([
        "```",
        "",
        f"The skill will: {skill['description']}",
        "",
    ])

    return base_prompt + "\n".join(skill_section)
