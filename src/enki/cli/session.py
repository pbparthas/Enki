"""Session commands: start, status, set-phase, set-goal, track-edit, end."""

import json
import sys
from pathlib import Path

from . import requires_db


@requires_db
def cmd_session_start(args):
    """Start a new session."""
    from ..session import start_session

    project_path = Path(args.project) if args.project else None
    session = start_session(project_path, args.goal)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id, "phase": session.phase,
            "tier": session.tier, "goal": session.goal,
        }))
    else:
        print(f"Session started: {session.session_id}")
        print(f"Phase: {session.phase}")
        print(f"Tier: {session.tier}")
        if session.goal:
            print(f"Goal: {session.goal}")


def cmd_session_status(args):
    """Show session status."""
    from ..session import get_session

    project_path = Path(args.project) if args.project else None
    session = get_session(project_path)
    if not session:
        print("No active session")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id, "phase": session.phase,
            "tier": session.tier, "goal": session.goal, "edits": session.edits,
        }))
    else:
        print(f"Session: {session.session_id}")
        print(f"Phase: {session.phase}")
        print(f"Tier: {session.tier}")
        print(f"Goal: {session.goal or '(none)'}")
        print(f"Files edited: {len(session.edits)}")
        if session.edits:
            print("Recent edits:")
            for f in session.edits[-5:]:
                print(f"  - {f}")


def cmd_session_set_phase(args):
    """Set session phase."""
    from ..session import set_phase

    project_path = Path(args.project) if args.project else None
    try:
        set_phase(args.phase, project_path)
        print(f"Phase set to: {args.phase}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_session_set_goal(args):
    """Set session goal."""
    from ..session import set_goal

    project_path = Path(args.project) if args.project else None
    set_goal(args.goal, project_path)
    print(f"Goal set: {args.goal}")


def cmd_session_track_edit(args):
    """Track a file edit."""
    from ..session import add_session_edit, get_tier, set_tier
    from ..enforcement import detect_tier

    project_path = Path(args.project) if args.project else None
    add_session_edit(args.file, project_path)

    old_tier = get_tier(project_path)
    new_tier = detect_tier(project_path=project_path)

    if new_tier != old_tier:
        from ..session import tier_escalated
        from ..violations import log_escalation, log_escalation_to_file

        if tier_escalated(old_tier, new_tier):
            log_escalation(old_tier, new_tier, project_path)
            log_escalation_to_file(old_tier, new_tier, project_path)
            print(f"ESCALATION: {old_tier} -> {new_tier}")

        set_tier(new_tier, project_path)


@requires_db
def cmd_session_end(args):
    """End session: reflect, archive, summarize."""
    from ..session import get_session

    project_path = Path(args.project) if args.project else None
    project_path = project_path or Path.cwd()

    session = get_session(project_path)
    if not session:
        print("No active session.")
        sys.exit(1)

    enki_dir = project_path / ".enki"
    results = {
        "session_id": session.session_id, "goal": session.goal,
        "phase": session.phase, "tier": session.tier,
        "files_edited": len(session.edits),
        "reflection": None, "feedback": None, "regressions": None, "archived": False,
    }

    # Step 1: Reflector
    try:
        from ..reflector import close_feedback_loop as reflect
        reflection_report = reflect(project_path)
        results["reflection"] = {
            "reflections": len(reflection_report.get("reflections", [])),
            "skills_stored": reflection_report.get("skills_stored", 0),
            "skills_duplicate": reflection_report.get("skills_duplicate", 0),
        }
    except ImportError:
        results["reflection"] = {"status": "module not available"}
    except Exception as e:
        results["reflection"] = {"status": f"error: {e}"}

    # Step 2: Feedback loop
    try:
        from ..feedback_loop import run_feedback_cycle
        feedback_report = run_feedback_cycle(project_path)
        results["feedback"] = {
            "proposals_generated": feedback_report.get("proposals_generated", 0),
            "status": feedback_report.get("status", "stable"),
        }
    except ImportError:
        results["feedback"] = {"status": "module not available"}
    except Exception as e:
        results["feedback"] = {"status": f"error: {e}"}

    # Step 3: Regression checks
    try:
        from ..feedback_loop import check_for_regressions
        results["regressions"] = check_for_regressions()
    except ImportError:
        results["regressions"] = {"status": "module not available"}
    except Exception as e:
        results["regressions"] = {"status": f"error: {e}"}

    # Step 4: Archive RUNNING.md
    running_path = enki_dir / "RUNNING.md"
    if running_path.exists():
        sessions_dir = enki_dir / "sessions"
        sessions_dir.mkdir(exist_ok=True)

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_name = f"{timestamp}_{session.session_id[:8]}.md"
        archive_path = sessions_dir / archive_name

        try:
            content = running_path.read_text()
            line_count = len([l for l in content.strip().split("\n") if l.strip()])

            with open(archive_path, "w") as f:
                f.write(f"# Session Archive: {session.session_id}\n")
                f.write(f"# Goal: {session.goal or '(none)'}\n")
                f.write(f"# Phase: {session.phase} | Tier: {session.tier}\n")
                f.write(f"# Files: {len(session.edits)} | Entries: {line_count}\n")
                f.write(f"# Archived: {datetime.now().isoformat()}\n\n")
                f.write(content)

            running_path.write_text("")
            results["archived"] = True
            results["archive_path"] = str(archive_path)
            results["entries_archived"] = line_count
        except Exception as e:
            results["archived"] = False
            results["archive_error"] = str(e)

    # Step 5: Output
    if args.json:
        print(json.dumps(results, default=str))
    else:
        print(f"\n{'=' * 50}")
        print(f"  Session End: {session.session_id[:8]}")
        print(f"{'=' * 50}")
        print(f"  Goal:    {session.goal or '(none)'}")
        print(f"  Phase:   {session.phase} → end")
        print(f"  Tier:    {session.tier}")
        print(f"  Files:   {len(session.edits)} edited")

        ref = results.get("reflection", {})
        if isinstance(ref, dict) and ref.get("skills_stored") is not None:
            print(f"  Reflect: {ref.get('reflections', 0)} insights, "
                  f"{ref['skills_stored']} beads stored "
                  f"({ref.get('skills_duplicate', 0)} deduped)")
        else:
            status = ref.get("status", "skipped") if isinstance(ref, dict) else "skipped"
            print(f"  Reflect: {status}")

        fb = results.get("feedback", {})
        if isinstance(fb, dict) and fb.get("proposals_generated") is not None:
            count = fb["proposals_generated"]
            if count > 0:
                print(f"  Feedback: {count} proposal(s) pending review")
            else:
                print(f"  Feedback: stable (no proposals)")
        else:
            status = fb.get("status", "skipped") if isinstance(fb, dict) else "skipped"
            print(f"  Feedback: {status}")

        reg = results.get("regressions")
        if isinstance(reg, list) and reg:
            print(f"  ⚠ Regressions: {len(reg)} flagged for review")

        if results.get("archived"):
            print(f"  Archive: {results.get('entries_archived', 0)} entries → sessions/")
        else:
            print(f"  Archive: no RUNNING.md to archive")

        print(f"{'=' * 50}\n")


def register(subparsers):
    """Register session commands."""
    session_parser = subparsers.add_parser("session", help="Session management")
    sub = session_parser.add_subparsers(dest="session_command")

    p = sub.add_parser("start", help="Start a new session")
    p.add_argument("-g", "--goal", help="Session goal")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_session_start)

    p = sub.add_parser("status", help="Show session status")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_session_status)

    p = sub.add_parser("set-phase", help="Set session phase")
    p.add_argument("phase", choices=["intake", "debate", "plan", "implement", "review", "test", "ship"])
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_session_set_phase)

    p = sub.add_parser("set-goal", help="Set session goal")
    p.add_argument("goal", help="Session goal")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_session_set_goal)

    p = sub.add_parser("track-edit", help="Track a file edit")
    p.add_argument("--file", required=True, help="File path")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_session_track_edit)

    p = sub.add_parser("end", help="End session: reflect, archive, summarize")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_session_end)
