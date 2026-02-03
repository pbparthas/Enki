"""Code Simplifier agent for cleaning AI-generated bloat.

Post-validation agent that simplifies code while preserving functionality.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess
import re


SIMPLIFIER_PROMPT = """# Code Simplifier

You are an expert code simplification specialist. Your job is to improve code
clarity, consistency, and maintainability while **preserving exact functionality**.

## CRITICAL RULES

1. **Never change behavior** - only change implementation
2. **Run tests before AND after** - verify nothing broke
3. **Focus on recently modified files** - don't touch unrelated code

## What to Look For

### Remove
- Duplicate code -> extract into reusable functions
- Redundant null checks that can't fail
- Over-complicated conditionals -> simplify to guard clauses
- Unnecessary try/catch blocks
- Comments that just repeat the code
- Dead code paths

### Simplify
- Nested ternaries -> if/else or switch
- Deep nesting -> early returns
- Verbose loops -> functional methods (map, filter, reduce)
- Magic numbers -> named constants

### Preserve
- Meaningful comments explaining WHY
- Error handling that IS necessary
- Type annotations and documentation
- Test coverage

## Process

1. Read the modified files
2. Run existing tests: `pytest` or appropriate command
3. Identify simplification opportunities
4. Make changes incrementally
5. Run tests after EACH change
6. If tests fail, revert immediately

## Output

Report what you simplified:
- Files modified
- Changes made (brief description)
- Lines reduced (approximate)
- Test results (all passing)
"""


@dataclass
class SimplificationResult:
    """Result of a simplification pass."""
    files_modified: list[str]
    changes_made: list[str]
    lines_reduced: int
    tests_passed: bool
    errors: list[str]


def get_modified_files(project_path: Path = None) -> list[str]:
    """Get list of recently modified files from git.

    Args:
        project_path: Project directory

    Returns:
        List of modified file paths
    """
    project_path = project_path or Path.cwd()

    # Get files modified in working tree
    result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    files = []
    if result.returncode == 0:
        files.extend(result.stdout.strip().split("\n"))

    # Get files in staging area
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        files.extend(result.stdout.strip().split("\n"))

    # Filter to source files only
    source_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
    filtered = [
        f for f in files
        if f and Path(f).suffix in source_extensions
    ]

    return list(set(filtered))  # Remove duplicates


def generate_simplifier_prompt(
    files: list[str] = None,
    all_modified: bool = False,
    project_path: Path = None,
) -> str:
    """Generate the prompt for the Simplifier agent.

    Args:
        files: Specific files to simplify
        all_modified: If True, simplify all modified files
        project_path: Project directory

    Returns:
        Formatted prompt for the Simplifier agent
    """
    project_path = project_path or Path.cwd()

    if all_modified:
        files = get_modified_files(project_path)
    elif not files:
        files = []

    prompt_parts = [SIMPLIFIER_PROMPT]

    if files:
        prompt_parts.append("\n## Files to Simplify\n")
        for f in files:
            prompt_parts.append(f"- {f}")
        prompt_parts.append("")
    else:
        prompt_parts.append("\n## Note")
        prompt_parts.append("No specific files provided. Check for recently modified files.")
        prompt_parts.append("")

    return "\n".join(prompt_parts)


def run_simplification(
    files: list[str] = None,
    all_modified: bool = False,
    project_path: Path = None,
) -> dict:
    """Get parameters to spawn Simplifier agent.

    Returns Task tool parameters for spawning Simplifier.

    Args:
        files: Specific files to simplify
        all_modified: If True, simplify all modified files
        project_path: Project directory

    Returns:
        Dict with Task tool parameters
    """
    project_path = project_path or Path.cwd()

    prompt = generate_simplifier_prompt(
        files=files,
        all_modified=all_modified,
        project_path=project_path,
    )

    if all_modified:
        files = get_modified_files(project_path)

    return {
        "description": "Simplifier: Clean AI-generated code",
        "prompt": prompt,
        "subagent_type": "general-purpose",
        "files": files or [],
    }


def parse_simplification_output(output: str) -> SimplificationResult:
    """Parse Simplifier agent output to extract results.

    Args:
        output: Raw output from Simplifier agent

    Returns:
        SimplificationResult with parsed data
    """
    files_modified = []
    changes_made = []
    lines_reduced = 0
    tests_passed = True
    errors = []

    # Look for files modified
    files_match = re.search(
        r"Files modified:(.+?)(?:Changes made:|$)",
        output,
        re.IGNORECASE | re.DOTALL
    )
    if files_match:
        files_section = files_match.group(1)
        for line in files_section.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                files_modified.append(line[2:].strip())

    # Look for changes made
    changes_match = re.search(
        r"Changes made:(.+?)(?:Lines reduced:|$)",
        output,
        re.IGNORECASE | re.DOTALL
    )
    if changes_match:
        changes_section = changes_match.group(1)
        for line in changes_section.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                changes_made.append(line[2:].strip())

    # Look for lines reduced
    lines_match = re.search(r"Lines reduced:?\s*~?(\d+)", output, re.IGNORECASE)
    if lines_match:
        lines_reduced = int(lines_match.group(1))

    # Look for test results
    if re.search(r"test.*(fail|error)", output, re.IGNORECASE):
        tests_passed = False
    elif re.search(r"all (tests )?pass", output, re.IGNORECASE):
        tests_passed = True

    # Look for errors
    error_match = re.search(r"Error[s]?:(.+?)(?:\n\n|$)", output, re.IGNORECASE | re.DOTALL)
    if error_match:
        error_section = error_match.group(1)
        for line in error_section.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                errors.append(line[2:].strip())

    return SimplificationResult(
        files_modified=files_modified,
        changes_made=changes_made,
        lines_reduced=lines_reduced,
        tests_passed=tests_passed,
        errors=errors,
    )
