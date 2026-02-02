"""Project Onboarding - Parse existing docs and create initial beads.

Analyzes existing project documentation to bootstrap Enki's knowledge:
- README.md for project overview
- ARCHITECTURE.md for design decisions
- CONTRIBUTING.md for coding standards
- Any existing docs/ folder
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .db import init_db
from .beads import create_bead


@dataclass
class ExtractedKnowledge:
    """A piece of knowledge extracted from docs."""
    source_file: str
    section: str
    content: str
    bead_type: str  # decision, solution, learning, pattern
    confidence: float


# Patterns that suggest different bead types
DECISION_PATTERNS = [
    r"we (?:chose|decided|picked|selected|use|went with)",
    r"(?:decision|choice):",
    r"(?:why|reason):",
    r"instead of",
    r"(?:over|rather than)",
    r"trade-?off",
]

SOLUTION_PATTERNS = [
    r"to (?:solve|fix|handle|address)",
    r"(?:solution|approach|implementation):",
    r"how (?:to|we)",
    r"(?:workaround|fix for)",
]

LEARNING_PATTERNS = [
    r"(?:note|important|remember|caveat|warning):",
    r"(?:gotcha|pitfall|trap)",
    r"(?:tip|hint|best practice)",
    r"don't|avoid|never",
    r"always|make sure",
]


def onboard_project(
    project_path: Path,
    dry_run: bool = False,
) -> list[ExtractedKnowledge]:
    """Onboard an existing project by extracting knowledge from docs.

    Args:
        project_path: Path to project root
        dry_run: If True, don't create beads, just return what would be created

    Returns:
        List of extracted knowledge items
    """
    init_db()

    extracted = []

    # Standard documentation files to check
    doc_files = [
        ("README.md", ["overview", "description", "features"]),
        ("ARCHITECTURE.md", ["architecture", "design", "components"]),
        ("CONTRIBUTING.md", ["contributing", "guidelines", "standards"]),
        ("CLAUDE.md", ["instructions", "guidelines"]),
        ("docs/ARCHITECTURE.md", ["architecture", "design"]),
        ("docs/DESIGN.md", ["design", "decisions"]),
        ("docs/ADR/*.md", ["decision", "adr"]),  # Architecture Decision Records
    ]

    for file_pattern, keywords in doc_files:
        if "*" in file_pattern:
            # Glob pattern
            for file_path in project_path.glob(file_pattern):
                if file_path.is_file():
                    knowledge = _extract_from_file(file_path, keywords, project_path)
                    extracted.extend(knowledge)
        else:
            file_path = project_path / file_pattern
            if file_path.exists():
                knowledge = _extract_from_file(file_path, keywords, project_path)
                extracted.extend(knowledge)

    # Also check for inline documentation patterns in code
    code_knowledge = _extract_from_code_comments(project_path)
    extracted.extend(code_knowledge)

    if not dry_run:
        # Create beads for extracted knowledge
        for item in extracted:
            if item.confidence >= 0.5:  # Only save reasonably confident extractions
                create_bead(
                    content=item.content,
                    bead_type=item.bead_type,
                    summary=f"[{item.section}] from {item.source_file}",
                    project=str(project_path),
                    context=f"Auto-extracted during onboarding from {item.source_file}",
                    tags=["onboarded", "auto-extracted", item.source_file.split("/")[-1]],
                )

    return extracted


def _extract_from_file(
    file_path: Path,
    keywords: list[str],
    project_path: Path,
) -> list[ExtractedKnowledge]:
    """Extract knowledge from a documentation file."""
    extracted = []

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return extracted

    relative_path = str(file_path.relative_to(project_path))

    # Split into sections by headers
    sections = _split_into_sections(content)

    for section_title, section_content in sections:
        if not section_content.strip():
            continue

        # Skip very short sections
        if len(section_content) < 50:
            continue

        # Determine bead type based on content patterns
        bead_type, confidence = _classify_content(section_content)

        # Boost confidence if section title contains keywords
        title_lower = section_title.lower()
        for keyword in keywords:
            if keyword in title_lower:
                confidence = min(confidence + 0.2, 1.0)
                break

        # Skip low confidence extractions
        if confidence < 0.3:
            continue

        # Clean up content
        clean_content = _clean_content(section_content)

        if len(clean_content) > 50:  # Meaningful content
            extracted.append(ExtractedKnowledge(
                source_file=relative_path,
                section=section_title or "Main",
                content=clean_content[:2000],  # Limit size
                bead_type=bead_type,
                confidence=confidence,
            ))

    return extracted


def _split_into_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into sections by headers."""
    sections = []

    # Match markdown headers
    header_pattern = r'^(#{1,3})\s+(.+)$'
    lines = content.split("\n")

    current_title = ""
    current_content = []

    for line in lines:
        match = re.match(header_pattern, line)
        if match:
            # Save previous section
            if current_content:
                sections.append((current_title, "\n".join(current_content)))

            current_title = match.group(2).strip()
            current_content = []
        else:
            current_content.append(line)

    # Don't forget last section
    if current_content:
        sections.append((current_title, "\n".join(current_content)))

    return sections


def _classify_content(content: str) -> tuple[str, float]:
    """Classify content into a bead type with confidence."""
    content_lower = content.lower()

    # Check for decision patterns
    decision_score = 0
    for pattern in DECISION_PATTERNS:
        if re.search(pattern, content_lower):
            decision_score += 1

    # Check for solution patterns
    solution_score = 0
    for pattern in SOLUTION_PATTERNS:
        if re.search(pattern, content_lower):
            solution_score += 1

    # Check for learning patterns
    learning_score = 0
    for pattern in LEARNING_PATTERNS:
        if re.search(pattern, content_lower):
            learning_score += 1

    # Determine type based on scores
    scores = {
        "decision": decision_score,
        "solution": solution_score,
        "learning": learning_score,
    }

    max_type = max(scores, key=scores.get)
    max_score = scores[max_type]

    if max_score == 0:
        # Default to learning for general documentation
        return "learning", 0.3

    # Calculate confidence based on how many patterns matched
    confidence = min(0.4 + (max_score * 0.15), 0.9)

    return max_type, confidence


def _clean_content(content: str) -> str:
    """Clean up extracted content."""
    # Remove excessive whitespace
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Remove markdown code block markers but keep content
    content = re.sub(r'```\w*\n?', '', content)

    # Remove HTML comments
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

    # Clean up leading/trailing whitespace
    content = content.strip()

    return content


def _extract_from_code_comments(project_path: Path) -> list[ExtractedKnowledge]:
    """Extract knowledge from code comments (TODO, FIXME, NOTE, etc.)."""
    extracted = []

    # Common code file patterns
    code_patterns = ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"]

    # Patterns to look for in comments
    comment_patterns = [
        (r'#\s*(?:TODO|FIXME|NOTE|IMPORTANT|WARNING):\s*(.+)', "learning"),
        (r'//\s*(?:TODO|FIXME|NOTE|IMPORTANT|WARNING):\s*(.+)', "learning"),
        (r'#\s*(?:DECISION|WHY):\s*(.+)', "decision"),
        (r'//\s*(?:DECISION|WHY):\s*(.+)', "decision"),
    ]

    for pattern in code_patterns:
        for file_path in project_path.glob(pattern):
            # Skip node_modules, venv, etc.
            if any(skip in str(file_path) for skip in [
                "node_modules", "venv", ".venv", "__pycache__",
                ".git", "dist", "build", ".next"
            ]):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                relative_path = str(file_path.relative_to(project_path))

                for regex, bead_type in comment_patterns:
                    for match in re.finditer(regex, content):
                        comment_text = match.group(1).strip()
                        if len(comment_text) > 20:  # Meaningful comment
                            extracted.append(ExtractedKnowledge(
                                source_file=relative_path,
                                section="Code comment",
                                content=comment_text,
                                bead_type=bead_type,
                                confidence=0.6,
                            ))

            except Exception:
                continue

    return extracted


def get_onboarding_preview(project_path: Path) -> str:
    """Get a preview of what onboarding would extract.

    Returns:
        Formatted string showing what would be extracted
    """
    extracted = onboard_project(project_path, dry_run=True)

    if not extracted:
        return "No knowledge found to extract from this project's documentation."

    lines = [
        f"## Onboarding Preview for {project_path.name}",
        f"Found {len(extracted)} pieces of knowledge to extract:\n",
    ]

    # Group by source file
    by_file = {}
    for item in extracted:
        if item.source_file not in by_file:
            by_file[item.source_file] = []
        by_file[item.source_file].append(item)

    for source, items in by_file.items():
        lines.append(f"### {source}")
        for item in items:
            confidence_bar = "█" * int(item.confidence * 5) + "░" * (5 - int(item.confidence * 5))
            lines.append(f"- [{item.bead_type}] {item.section} [{confidence_bar}]")
            preview = item.content[:100].replace("\n", " ")
            lines.append(f"  \"{preview}...\"")
        lines.append("")

    lines.append("\nRun 'enki onboard --confirm' to create these beads.")

    return "\n".join(lines)


def get_onboarding_status(project_path: Path) -> dict:
    """Check if a project has been onboarded.

    Returns:
        Dict with onboarding status info
    """
    init_db()
    from .db import get_db

    db = get_db()
    project_str = str(project_path)

    # Check for onboarded beads
    row = db.execute("""
        SELECT COUNT(*) as count
        FROM beads
        WHERE project = ?
        AND tags LIKE '%onboarded%'
    """, (project_str,)).fetchone()

    onboarded_count = row["count"]

    # Check for .enki directory
    enki_dir = project_path / ".enki"
    has_enki_dir = enki_dir.exists()

    # Check for common doc files
    doc_files = ["README.md", "ARCHITECTURE.md", "CONTRIBUTING.md"]
    available_docs = [f for f in doc_files if (project_path / f).exists()]

    return {
        "onboarded": onboarded_count > 0,
        "bead_count": onboarded_count,
        "has_enki_dir": has_enki_dir,
        "available_docs": available_docs,
    }
