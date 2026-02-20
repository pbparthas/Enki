"""spec_review.py — Implementation Spec review gate.

InfoSec review is mandatory for Standard/Full tiers.
UI/UX and Performance reviews are conditional based on heuristics.
EM brokers specialist concerns back to Architect for revision.

Flow: Architect produces spec → specialists review → concerns routed
back to Architect → Architect revises → Validator red-cell → HITL.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Heuristic triggers for conditional specialists
_UI_TRIGGERS = {
    "keywords": [
        "frontend", "ui", "ux", "component", "page", "view", "layout",
        "modal", "dialog", "form", "button", "input", "dropdown",
        "navigation", "sidebar", "header", "footer", "responsive",
        "css", "style", "theme", "animation", "dashboard",
    ],
    "extensions": [".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html"],
    "dirs": ["components/", "pages/", "views/", "styles/", "layouts/"],
}

_PERF_TRIGGERS = {
    "keywords": [
        "performance", "sla", "latency", "throughput", "benchmark",
        "cache", "caching", "optimization", "profiling", "p99", "p95",
        "concurrent", "parallel", "async", "batch", "queue", "rate limit",
        "index", "query optimization", "connection pool", "load",
    ],
}


def should_review_infosec(tier: str) -> bool:
    """InfoSec review is mandatory for Standard and Full tiers."""
    return tier in ("standard", "full")


def should_review_ui_ux(spec_text: str, files: list[str] | None = None) -> bool:
    """Heuristic: should UI/UX specialist review the spec?

    Triggers on frontend components, routes, user-facing APIs.
    """
    text = spec_text.lower()

    # Keyword check
    if any(kw in text for kw in _UI_TRIGGERS["keywords"]):
        return True

    # File extension check
    if files:
        for f in files:
            for ext in _UI_TRIGGERS["extensions"]:
                if f.endswith(ext):
                    return True
            for d in _UI_TRIGGERS["dirs"]:
                if d in f:
                    return True

    return False


def should_review_performance(spec_text: str) -> bool:
    """Heuristic: should Performance specialist review the spec?

    Triggers on SLAs, throughput, caching requirements.
    """
    text = spec_text.lower()
    return any(kw in text for kw in _PERF_TRIGGERS["keywords"])


def determine_reviewers(
    tier: str,
    spec_text: str,
    files: list[str] | None = None,
) -> list[dict]:
    """Determine which specialists should review the Implementation Spec.

    Returns list of reviewers with reason and mandatory flag.
    """
    reviewers = []

    # InfoSec: mandatory for Standard/Full
    if should_review_infosec(tier):
        reviewers.append({
            "role": "infosec",
            "mandatory": True,
            "reason": f"InfoSec review mandatory for {tier} tier",
        })

    # UI/UX: conditional
    if should_review_ui_ux(spec_text, files):
        reviewers.append({
            "role": "ui_ux",
            "mandatory": False,
            "reason": "Frontend/UI components detected in spec",
        })

    # Performance: conditional
    if should_review_performance(spec_text):
        reviewers.append({
            "role": "performance",
            "mandatory": False,
            "reason": "Performance/SLA requirements detected in spec",
        })

    return reviewers


def format_concerns_for_architect(
    concerns: list[dict],
    reviewer_role: str,
) -> str:
    """Format specialist concerns as a revision request for Architect.

    Args:
        concerns: List of concern dicts from specialist output.
        reviewer_role: Which specialist raised the concerns.

    Returns formatted message for Architect mail thread.
    """
    if not concerns:
        return f"{reviewer_role} review: No concerns raised. Approved."

    lines = [
        f"## {reviewer_role.upper()} Review — Revision Required",
        "",
    ]
    for i, concern in enumerate(concerns, 1):
        severity = concern.get("severity", "medium")
        title = concern.get("title", "Untitled concern")
        description = concern.get("description", concern.get("content", ""))
        lines.append(f"{i}. [{severity.upper()}] {title}")
        if description:
            lines.append(f"   {description}")
        lines.append("")

    lines.append("Architect: Please revise the Implementation Spec to address these concerns.")
    return "\n".join(lines)


def check_spec_for_ac_codes(spec_text: str) -> dict:
    """Check if Implementation Spec contains acceptance criteria codes.

    AC codes follow format: AC-{section}-{sequence}

    Returns:
        {
            "has_ac": bool,
            "ac_codes": list[str],
            "sections_without_ac": list[str],  (approximate)
        }
    """
    import re
    ac_pattern = re.compile(r"AC-\w+-\d+")
    ac_codes = ac_pattern.findall(spec_text)

    # Try to detect section headers (## or ###)
    section_pattern = re.compile(r"^#{2,3}\s+(.+)$", re.MULTILINE)
    sections = section_pattern.findall(spec_text)

    # Check which sections might lack AC codes (rough heuristic)
    sections_without = []
    if sections:
        lines = spec_text.split("\n")
        current_section = None
        section_has_ac = {}
        for line in lines:
            sec_match = section_pattern.match(line)
            if sec_match:
                current_section = sec_match.group(1).strip()
                section_has_ac[current_section] = False
            elif current_section and ac_pattern.search(line):
                section_has_ac[current_section] = True

        sections_without = [
            s for s, has in section_has_ac.items() if not has
        ]

    return {
        "has_ac": len(ac_codes) > 0,
        "ac_codes": ac_codes,
        "ac_count": len(ac_codes),
        "sections_without_ac": sections_without,
    }
