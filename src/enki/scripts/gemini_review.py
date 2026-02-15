"""gemini_review.py — CLI for generating/applying external Gemini reviews.

No API calls — generates a package that the human pipes to Gemini.
Supports full quarterly review, project-scoped mini review,
response validation, and report generation.

Usage:
    python -m enki.scripts.gemini_review                       # Full review package
    python -m enki.scripts.gemini_review --mini <project>      # Project-scoped mini review
    python -m enki.scripts.gemini_review --validate resp.json  # Validate response format
    python -m enki.scripts.gemini_review --apply resp.json     # Apply review decisions
    python -m enki.scripts.gemini_review --report resp.json    # Generate markdown report
"""

import json
import sys
from pathlib import Path

from enki.memory.gemini import (
    apply_promotions,
    generate_review_package,
    generate_review_report,
    prepare_mini_review,
    process_review_response,
    validate_gemini_response,
)


def cmd_generate(output_dir: str | None = None):
    """Generate full quarterly review package.

    Writes markdown file to ~/.enki/reviews/ and prints the path.
    """
    filepath = generate_review_package(output_dir)
    print(f"Review package written to: {filepath}")
    print("Send this file to your review LLM and save the JSON response.")


def cmd_mini(project: str):
    """Generate project-scoped mini review package.

    Lighter weight than full review — for mid-project checkpoints.
    """
    filepath = prepare_mini_review(project)
    print(f"Mini review package written to: {filepath}")


def cmd_validate(response_path: str):
    """Validate that a review response is well-formed JSON.

    Checks structure, required fields, and valid action values.
    Exits 0 if valid, 1 if invalid.
    """
    path = Path(response_path)
    if not path.exists():
        print(f"File not found: {response_path}", file=sys.stderr)
        sys.exit(1)

    response_text = path.read_text()
    result = validate_gemini_response(response_text)

    if result["valid"]:
        parsed = result["parsed"]
        bead_count = len(parsed.get("bead_decisions", []))
        proposal_count = len(parsed.get("proposal_decisions", []))
        print(f"Valid response: {bead_count} bead decisions, {proposal_count} proposal decisions")
    else:
        print("Invalid response:", file=sys.stderr)
        for error in result["errors"]:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)


def cmd_apply(response_path: str):
    """Apply Gemini's review response — promote, discard, flag beads."""
    path = Path(response_path)
    if not path.exists():
        print(f"File not found: {response_path}", file=sys.stderr)
        sys.exit(1)

    response_text = path.read_text()

    # Validate first
    validation = validate_gemini_response(response_text)
    if not validation["valid"]:
        print("Response validation failed:", file=sys.stderr)
        for error in validation["errors"]:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    result = process_review_response(response_text)
    print("Review applied:")
    print(f"  Promoted: {result.get('promoted', 0)}")
    print(f"  Discarded: {result.get('discarded', 0)}")
    print(f"  Flagged: {result.get('flagged', 0)}")
    print(f"  Proposals approved: {result.get('proposals_approved', 0)}")
    print(f"  Proposals rejected: {result.get('proposals_rejected', 0)}")


def cmd_report(response_path: str):
    """Generate a markdown report from review response."""
    path = Path(response_path)
    if not path.exists():
        print(f"File not found: {response_path}", file=sys.stderr)
        sys.exit(1)

    response_text = path.read_text()
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    actions = parsed.get("bead_decisions", [])
    report = generate_review_report(actions)
    print(report)


def main():
    args = sys.argv[1:]

    if not args:
        cmd_generate()
        return

    command = args[0]

    if command == "--mini":
        if len(args) < 2:
            print("Usage: gemini_review --mini <project>", file=sys.stderr)
            sys.exit(1)
        cmd_mini(args[1])
    elif command == "--validate":
        if len(args) < 2:
            print("Usage: gemini_review --validate <response.json>", file=sys.stderr)
            sys.exit(1)
        cmd_validate(args[1])
    elif command == "--apply":
        if len(args) < 2:
            print("Usage: gemini_review --apply <response.json>", file=sys.stderr)
            sys.exit(1)
        cmd_apply(args[1])
    elif command == "--report":
        if len(args) < 2:
            print("Usage: gemini_review --report <response.json>", file=sys.stderr)
            sys.exit(1)
        cmd_report(args[1])
    elif command == "--output-dir":
        if len(args) < 2:
            print("Usage: gemini_review --output-dir <dir>", file=sys.stderr)
            sys.exit(1)
        cmd_generate(output_dir=args[1])
    elif command in ("--help", "-h"):
        print(__doc__)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
