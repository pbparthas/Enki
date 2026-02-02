"""Command-line interface for Enki."""

import argparse
import sys
import json

from pathlib import Path

from .db import init_db, get_db, DB_PATH, ENKI_DIR
from .beads import create_bead, get_bead, star_bead, get_recent_beads
from .search import search
from .retention import maintain_wisdom
from .session import (
    start_session, get_session, get_phase, set_phase, get_tier, set_tier,
    get_goal, set_goal, add_session_edit, get_session_edits,
)
from .enforcement import check_all_gates, detect_tier
from .violations import get_violation_stats
from .pm import (
    generate_perspectives, check_perspectives_complete,
    create_spec, get_spec, list_specs, is_spec_approved, approve_spec,
    decompose_spec, save_task_graph, load_task_graph, get_orchestration_status,
)
from .orchestrator import (
    start_orchestration, load_orchestration, save_orchestration,
    start_task, complete_task, fail_task,
    file_bug, assign_bug, start_bug_verification, close_bug, reopen_bug, get_open_bugs,
    escalate_to_hitl, resolve_hitl, check_hitl_required,
    get_full_orchestration_status, get_next_action,
    AGENTS,
)
from .persona import (
    build_session_start_injection,
    build_error_context_injection,
    build_decision_context,
    generate_session_summary,
    get_enki_greeting,
)
from .evolution import (
    init_evolution_log,
    analyze_violation_patterns,
    analyze_escalation_patterns,
    check_correction_triggers,
    run_weekly_self_review,
    get_evolution_summary,
    explain_block,
    get_self_awareness_response,
    is_review_due,
    get_last_review_date,
)
from .ereshkigal import (
    init_patterns,
    load_patterns,
    add_pattern,
    remove_pattern,
    get_pattern_categories,
    intercept,
    would_block,
    mark_false_positive,
    mark_legitimate,
    get_interception_stats,
    get_recent_interceptions,
    generate_weekly_report,
    is_review_overdue,
    get_review_reminder,
    find_evasions_with_bugs,
    generate_fresh_claude_prompt,
    generate_review_checklist,
    complete_review,
    get_report_summary,
)


def cmd_init(args):
    """Initialize Enki database."""
    init_db()
    print(f"Initialized Enki at {ENKI_DIR}")
    print(f"Database: {DB_PATH}")


def cmd_remember(args):
    """Store a new bead."""
    init_db()

    tags = args.tags.split(",") if args.tags else None

    bead = create_bead(
        content=args.content,
        bead_type=args.type,
        summary=args.summary,
        project=args.project,
        context=args.context,
        tags=tags,
        starred=args.starred,
    )

    print(f"Remembered [{bead.type}] {bead.id}")
    if args.verbose:
        print(f"Content: {bead.content[:200]}{'...' if len(bead.content) > 200 else ''}")


def cmd_recall(args):
    """Search for beads."""
    init_db()

    results = search(
        query=args.query,
        project=args.project,
        bead_type=args.type,
        limit=args.limit,
    )

    if not results:
        print("No results found.")
        return

    for i, result in enumerate(results, 1):
        bead = result.bead
        sources = "+".join(result.sources)
        starred = "*" if bead.starred else ""

        print(f"{i}. [{bead.type}]{starred} (score: {result.score:.2f}, {sources})")
        print(f"   {bead.summary or bead.content[:100]}{'...' if len(bead.content) > 100 else ''}")
        print(f"   ID: {bead.id}")
        print()


def cmd_status(args):
    """Show memory status."""
    init_db()
    db = get_db()

    total = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()["count"]
    active = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE superseded_by IS NULL"
    ).fetchone()["count"]
    starred = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE starred = 1"
    ).fetchone()["count"]

    by_type = db.execute(
        "SELECT type, COUNT(*) as count FROM beads WHERE superseded_by IS NULL GROUP BY type"
    ).fetchall()

    print("Enki Memory Status")
    print("=" * 40)
    print(f"Database: {DB_PATH}")
    print(f"Total beads: {total}")
    print(f"Active beads: {active}")
    print(f"Starred beads: {starred}")
    print()
    print("By type:")
    for row in by_type:
        print(f"  {row['type']}: {row['count']}")


def cmd_recent(args):
    """Show recent beads."""
    init_db()

    beads = get_recent_beads(limit=args.limit, project=args.project)

    if not beads:
        print("No beads found.")
        return

    for i, bead in enumerate(beads, 1):
        starred = "*" if bead.starred else ""
        print(f"{i}. [{bead.type}]{starred} {bead.id[:8]}...")
        print(f"   {bead.summary or bead.content[:100]}{'...' if len(bead.content) > 100 else ''}")
        print(f"   Created: {bead.created_at}")
        print()


def cmd_star(args):
    """Star a bead."""
    init_db()

    bead = star_bead(args.bead_id)
    if bead:
        print(f"Starred bead {bead.id}")
    else:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)


def cmd_get(args):
    """Get a specific bead."""
    init_db()

    bead = get_bead(args.bead_id)
    if not bead:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "id": bead.id,
            "type": bead.type,
            "content": bead.content,
            "summary": bead.summary,
            "project": bead.project,
            "starred": bead.starred,
            "tags": bead.tags,
            "weight": bead.weight,
            "created_at": str(bead.created_at),
            "last_accessed": str(bead.last_accessed) if bead.last_accessed else None,
        }, indent=2))
    else:
        print(f"ID: {bead.id}")
        print(f"Type: {bead.type}")
        print(f"Starred: {bead.starred}")
        print(f"Weight: {bead.weight:.2f}")
        print(f"Project: {bead.project or '(global)'}")
        print(f"Tags: {', '.join(bead.tags) if bead.tags else '(none)'}")
        print(f"Created: {bead.created_at}")
        print(f"Last accessed: {bead.last_accessed or '(never)'}")
        print()
        print("Content:")
        print("-" * 40)
        print(bead.content)
        if bead.summary:
            print()
            print("Summary:")
            print("-" * 40)
            print(bead.summary)


def cmd_maintain(args):
    """Run maintenance tasks."""
    init_db()

    results = maintain_wisdom()
    print("Maintenance complete:")
    print(f"  Weights updated: {results['weights_updated']}")
    print(f"  Beads archived: {results['archived']}")
    print(f"  Superseded purged: {results['purged']}")


# === Session Commands ===

def cmd_session_start(args):
    """Start a new session."""
    init_db()
    project_path = Path(args.project) if args.project else None

    session = start_session(project_path, args.goal)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id,
            "phase": session.phase,
            "tier": session.tier,
            "goal": session.goal,
        }))
    else:
        print(f"Session started: {session.session_id}")
        print(f"Phase: {session.phase}")
        print(f"Tier: {session.tier}")
        if session.goal:
            print(f"Goal: {session.goal}")


def cmd_session_status(args):
    """Show session status."""
    project_path = Path(args.project) if args.project else None

    session = get_session(project_path)
    if not session:
        print("No active session")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id,
            "phase": session.phase,
            "tier": session.tier,
            "goal": session.goal,
            "edits": session.edits,
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
    project_path = Path(args.project) if args.project else None

    try:
        set_phase(args.phase, project_path)
        print(f"Phase set to: {args.phase}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_session_set_goal(args):
    """Set session goal."""
    project_path = Path(args.project) if args.project else None

    set_goal(args.goal, project_path)
    print(f"Goal set: {args.goal}")


def cmd_session_track_edit(args):
    """Track a file edit."""
    project_path = Path(args.project) if args.project else None

    edits = add_session_edit(args.file, project_path)

    # Recalculate tier
    old_tier = get_tier(project_path)
    new_tier = detect_tier(project_path=project_path)

    if new_tier != old_tier:
        from .session import tier_escalated
        from .violations import log_escalation, log_escalation_to_file

        if tier_escalated(old_tier, new_tier):
            log_escalation(old_tier, new_tier, project_path)
            log_escalation_to_file(old_tier, new_tier, project_path)
            print(f"ESCALATION: {old_tier} -> {new_tier}")

        set_tier(new_tier, project_path)


# === Gate Commands ===

def cmd_gate_check(args):
    """Check if a tool use is allowed."""
    init_db()
    project_path = Path(args.project) if args.project else None

    result = check_all_gates(
        tool=args.tool,
        file_path=args.file if args.file else None,
        agent_type=args.agent if args.agent else None,
        project_path=project_path,
    )

    if args.json:
        if result.allowed:
            print(json.dumps({"decision": "allow"}))
        else:
            print(json.dumps({
                "decision": "block",
                "reason": result.reason,
            }))
    else:
        if result.allowed:
            print("ALLOWED")
        else:
            print(f"BLOCKED by gate: {result.gate}")
            print(result.reason)
            sys.exit(1)


def cmd_gate_stats(args):
    """Show gate violation statistics."""
    init_db()

    stats = get_violation_stats(days=args.days)

    print(f"Violation Statistics (last {args.days} days)")
    print("=" * 40)
    print(f"Total violations: {stats['total_violations']}")
    print(f"Override rate: {stats['override_rate']:.1%}")
    print(f"Tier escalations: {stats['escalations']}")
    print()
    print("By gate:")
    for gate, count in stats['by_gate'].items():
        print(f"  {gate}: {count}")


# === PM Commands ===

def cmd_debate(args):
    """Start debate phase - generate perspectives."""
    init_db()
    project_path = Path(args.project) if args.project else None

    # Set phase to debate
    set_phase("debate", project_path)

    perspectives_path = generate_perspectives(
        goal=args.goal,
        context=args.context,
        project_path=project_path,
    )

    print(f"Debate started for: {args.goal}")
    print(f"Perspectives template created: {perspectives_path}")
    print()
    print("Fill in ALL perspectives before running 'enki plan':")
    print("  - PM Perspective")
    print("  - CTO Perspective")
    print("  - Architect Perspective")
    print("  - DBA Perspective")
    print("  - Security Perspective")
    print("  - Devil's Advocate")
    print()
    print("Check status with: enki debate --check")


def cmd_debate_check(args):
    """Check if debate perspectives are complete."""
    project_path = Path(args.project) if args.project else None

    is_complete, missing = check_perspectives_complete(project_path)

    if is_complete:
        print("All perspectives complete. Ready for 'enki plan'.")
    else:
        print("Perspectives incomplete. Missing:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def cmd_plan(args):
    """Create a spec from debate."""
    init_db()
    project_path = Path(args.project) if args.project else None

    try:
        spec_path = create_spec(
            name=args.name,
            problem=args.problem,
            solution=args.solution,
            project_path=project_path,
        )

        print(f"Spec created: {spec_path}")
        print()
        print("Edit the spec to fill in details, then run:")
        print(f"  enki approve {args.name}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_approve(args):
    """Approve a spec."""
    init_db()
    project_path = Path(args.project) if args.project else None

    try:
        approve_spec(args.name, project_path)
        print(f"Spec approved: {args.name}")
        print("Phase transitioned to: implement")
        print()
        print("Gate 2 (Spec Approval) is now satisfied.")
        print("You can now spawn implementation agents.")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_specs(args):
    """List all specs."""
    project_path = Path(args.project) if args.project else None

    specs = list_specs(project_path)

    if not specs:
        print("No specs found.")
        return

    print("Specs:")
    for spec in specs:
        status = "[APPROVED]" if spec["approved"] else "[pending]"
        print(f"  {status} {spec['name']}")
        if args.verbose:
            print(f"         {spec['path']}")


def cmd_spec_show(args):
    """Show a spec."""
    project_path = Path(args.project) if args.project else None

    content = get_spec(args.name, project_path)
    if content:
        print(content)
    else:
        print(f"Spec not found: {args.name}")
        sys.exit(1)


def cmd_decompose(args):
    """Decompose spec into task graph."""
    init_db()
    project_path = Path(args.project) if args.project else None

    try:
        # Check if spec is approved
        if not is_spec_approved(args.name, project_path):
            print(f"Spec not approved: {args.name}")
            print("Run 'enki approve {name}' first.")
            sys.exit(1)

        graph = decompose_spec(args.name, project_path)
        save_task_graph(graph, project_path)

        print(f"Task graph created for: {args.name}")
        print()

        waves = graph.get_waves()
        for i, wave in enumerate(waves, 1):
            print(f"Wave {i}:")
            for task in wave:
                deps = ", ".join(task.dependencies) if task.dependencies else "none"
                print(f"  - {task.id}: {task.description} ({task.agent})")
                print(f"    Dependencies: {deps}")
                if task.files_in_scope:
                    print(f"    Files: {', '.join(task.files_in_scope)}")
            print()

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_orchestration_status(args):
    """Show orchestration status."""
    project_path = Path(args.project) if args.project else None

    status = get_full_orchestration_status(project_path)

    if not status["active"]:
        print("No active orchestration.")
        print("Run 'enki orchestrate <spec-name>' to start one.")
        return

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"Orchestration: {status['spec']} ({status['orchestration_id']})")
        print(f"Status: {status['status']}")
        print(f"Wave: {status['current_wave']}")
        print(f"Progress: {status['tasks']['completed']}/{status['tasks']['total']} tasks ({status['tasks']['progress']:.0%})")
        print()

        if status['hitl']['required']:
            print("⚠️  HUMAN INTERVENTION REQUIRED")
            print(f"   Reason: {status['hitl']['reason']}")
            print()

        if status['bugs']['open'] > 0:
            print(f"Bugs: {status['bugs']['open']} open ({status['bugs']['critical']} critical)")
            print()

        if status['tasks']['ready']:
            print(f"Ready tasks: {', '.join(status['tasks']['ready'])}")

        next_action = get_next_action(project_path)
        print()
        print(f"Next: {next_action['message']}")


def cmd_orchestrate_start(args):
    """Start orchestration from an approved spec."""
    init_db()
    project_path = Path(args.project) if args.project else None

    try:
        # Get task graph from spec
        graph = decompose_spec(args.name, project_path)

        # Start orchestration
        orch = start_orchestration(args.name, graph, project_path)

        print(f"Orchestration started: {orch.id}")
        print(f"Spec: {args.name}")
        print(f"Tasks: {len(graph.tasks)}")
        print()

        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_start(args):
    """Start a task."""
    project_path = Path(args.project) if args.project else None

    try:
        task = start_task(args.task_id, project_path)

        print(f"Task started: {task.id}")
        print(f"Agent: {task.agent}")
        print(f"Description: {task.description}")

        if task.agent in AGENTS:
            agent_info = AGENTS[task.agent]
            print(f"Role: {agent_info['role']}")
            print(f"Tools: {', '.join(agent_info['tools'])}")

        if task.files_in_scope:
            print(f"Files: {', '.join(task.files_in_scope)}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_complete(args):
    """Complete a task."""
    project_path = Path(args.project) if args.project else None

    try:
        task = complete_task(args.task_id, args.output, project_path)

        print(f"Task completed: {task.id}")

        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_fail(args):
    """Mark a task as failed."""
    project_path = Path(args.project) if args.project else None

    try:
        task = fail_task(args.task_id, args.reason, project_path)

        if task.status == "failed":
            print(f"Task failed (max attempts reached): {task.id}")
            print("HITL escalation triggered.")
        else:
            print(f"Task will retry: {task.id} (attempt {task.attempts}/{task.max_attempts})")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_file(args):
    """File a new bug."""
    project_path = Path(args.project) if args.project else None

    try:
        bug = file_bug(
            title=args.title,
            description=args.description or args.title,
            found_by=args.found_by,
            severity=args.severity,
            related_task=args.task,
            project_path=project_path,
        )

        print(f"Bug filed: {bug.id}")
        print(f"Title: {bug.title}")
        print(f"Severity: {bug.severity}")
        print(f"Assigned to: {bug.assigned_to}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_list(args):
    """List bugs."""
    project_path = Path(args.project) if args.project else None

    bugs = get_open_bugs(project_path)

    if not bugs:
        print("No open bugs.")
        return

    print("Open Bugs:")
    for bug in bugs:
        severity_marker = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(bug.severity, "⚪")
        print(f"  {severity_marker} {bug.id}: {bug.title}")
        print(f"     Status: {bug.status} | Assigned: {bug.assigned_to} | Cycle: {bug.cycle}/{bug.max_cycles}")


def cmd_bug_close(args):
    """Close a bug."""
    project_path = Path(args.project) if args.project else None

    try:
        bug = close_bug(args.bug_id, args.resolution, project_path)
        print(f"Bug closed: {bug.id} ({bug.resolution})")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_reopen(args):
    """Reopen a bug (verification failed)."""
    project_path = Path(args.project) if args.project else None

    try:
        bug = reopen_bug(args.bug_id, project_path)

        if bug.status == "hitl":
            print(f"Bug escalated to HITL: {bug.id}")
            print(f"Max cycles ({bug.max_cycles}) exceeded.")
        else:
            print(f"Bug reopened: {bug.id} (cycle {bug.cycle}/{bug.max_cycles})")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_hitl_status(args):
    """Check HITL status."""
    project_path = Path(args.project) if args.project else None

    required, reason = check_hitl_required(project_path)

    if required:
        print("⚠️  HUMAN INTERVENTION REQUIRED")
        print(f"Reason: {reason}")
        print()
        print("Resolve with: enki hitl resolve --reason 'resolution details'")
    else:
        print("No HITL required.")


def cmd_hitl_resolve(args):
    """Resolve HITL escalation."""
    project_path = Path(args.project) if args.project else None

    try:
        resolve_hitl(args.reason, project_path)
        print(f"HITL resolved: {args.reason}")

        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_next(args):
    """Show the next recommended action."""
    project_path = Path(args.project) if args.project else None

    action = get_next_action(project_path)

    if args.json:
        print(json.dumps(action, indent=2))
    else:
        print(f"Action: {action['action']}")
        print(f"Message: {action['message']}")

        if action['action'] == 'run_task':
            print(f"Task: {action['task_id']}")
            print(f"Agent: {action['agent']}")
        elif action['action'] == 'fix_bug':
            print(f"Bug: {action['bug_id']}")


def cmd_agents(args):
    """List available agents."""
    print("Available Agents:")
    print()
    for name, info in AGENTS.items():
        tier_marker = {"CRITICAL": "★", "STANDARD": "◆", "CONDITIONAL": "○"}.get(info['tier'], " ")
        print(f"  {tier_marker} {name}")
        print(f"    Role: {info['role']}")
        print(f"    Tier: {info['tier']}")
        print(f"    Tools: {', '.join(info['tools'])}")
        if 'skill' in info:
            print(f"    Skill: {info['skill']}")
        print()


# === Persona Commands ===

def cmd_context(args):
    """Show Enki's context injection."""
    project_path = Path(args.project) if args.project else None

    context = build_session_start_injection(project_path)
    print(context)


def cmd_greeting(args):
    """Show Enki's greeting."""
    project_path = Path(args.project) if args.project else None

    greeting = get_enki_greeting(project_path)
    print(greeting)


def cmd_summary(args):
    """Show session summary."""
    project_path = Path(args.project) if args.project else None

    summary = generate_session_summary(project_path)
    print(summary)


def cmd_error_context(args):
    """Show context for an error."""
    project_path = Path(args.project) if args.project else None

    context = build_error_context_injection(args.error, project_path)
    print(context)


def cmd_decision_context(args):
    """Show context for a decision."""
    project_path = Path(args.project) if args.project else None

    context = build_decision_context(args.topic, project_path)
    print(context)


# === Evolution Commands ===

def cmd_evolution_summary(args):
    """Show evolution summary."""
    project_path = Path(args.project) if args.project else None

    summary = get_evolution_summary(project_path)
    print(summary)


def cmd_evolution_patterns(args):
    """Show violation patterns."""
    project_path = Path(args.project) if args.project else None

    patterns = analyze_violation_patterns(days=args.days, project_path=project_path)

    if not patterns:
        print("No violation patterns found.")
        return

    print(f"Violation Patterns (last {args.days} days)")
    print("=" * 40)
    for p in patterns:
        print(f"\nGate: {p['gate']} ({p['total']} violations)")
        for reason in p.get('reasons', [])[:3]:
            print(f"  - {reason['reason'][:50]}: {reason['count']}x")


def cmd_evolution_triggers(args):
    """Check for self-correction triggers."""
    project_path = Path(args.project) if args.project else None

    triggers = check_correction_triggers(project_path)

    if not triggers:
        print("No correction triggers detected.")
        return

    print("Self-Correction Triggers")
    print("=" * 40)
    for t in triggers:
        print(f"\n{t['trigger'].upper()}")
        print(f"  {t['suggestion']}")


def cmd_evolution_review(args):
    """Run weekly self-review."""
    init_db()
    project_path = Path(args.project) if args.project else None

    print("Running Enki self-review...")
    print()

    report = run_weekly_self_review(project_path)

    print(f"Review Date: {report['date']}")
    print()

    if report['violation_patterns']:
        print(f"Violation Patterns: {len(report['violation_patterns'])}")
        for p in report['violation_patterns'][:3]:
            print(f"  - {p['gate']}: {p['total']} violations")

    if report['escalation_patterns']:
        print(f"\nEscalation Patterns: {len(report['escalation_patterns'])}")
        for p in report['escalation_patterns'][:3]:
            print(f"  - '{p['goal_pattern'][:30]}': {p['count']} escalations")

    if report['corrections_made']:
        print(f"\nCorrections Made: {len(report['corrections_made'])}")
        for c in report['corrections_made']:
            print(f"  - {c['correction']}")

    if report['recommendations']:
        print(f"\nRecommendations: {len(report['recommendations'])}")
        for r in report['recommendations']:
            print(f"  - {r['description']}")

    if not any([report['violation_patterns'], report['escalation_patterns'],
                report['corrections_made'], report['recommendations']]):
        print("No significant patterns detected. Keep up the good work!")


def cmd_evolution_explain(args):
    """Explain a blocking decision."""
    project_path = Path(args.project) if args.project else None

    explanation = explain_block(args.gate, args.reason or "", project_path)
    print(explanation)


def cmd_evolution_ask(args):
    """Ask Enki about her behavior."""
    project_path = Path(args.project) if args.project else None

    response = get_self_awareness_response(args.question, project_path)
    print(response)


def cmd_evolution_status(args):
    """Check if review is due."""
    project_path = Path(args.project) if args.project else None

    last_review = get_last_review_date(project_path)
    due = is_review_due(project_path)

    print(f"Last Review: {last_review or 'Never'}")
    print(f"Review Due: {'Yes' if due else 'No'}")

    if due:
        print("\nRun 'enki evolution review' to perform self-review.")


# === Ereshkigal Commands ===

def cmd_ereshkigal_test(args):
    """Test if reasoning would be blocked."""
    result = would_block(args.reasoning)

    if result:
        category, pattern = result
        print(f"WOULD BLOCK")
        print(f"  Category: {category}")
        print(f"  Pattern: {pattern}")
        sys.exit(1)
    else:
        print("WOULD ALLOW")


def cmd_ereshkigal_intercept(args):
    """Run interception on reasoning."""
    init_db()

    result = intercept(
        tool=args.tool,
        reasoning=args.reasoning,
        session_id=args.session,
        phase=args.phase,
    )

    if args.json:
        print(json.dumps({
            "allowed": result.allowed,
            "category": result.category,
            "pattern": result.pattern,
            "interception_id": result.interception_id,
            "message": result.message,
        }))
    else:
        if result.allowed:
            print("ALLOWED")
            print(f"Logged: {result.interception_id[:8] if result.interception_id else 'N/A'}")
        else:
            print(result.message)
            sys.exit(1)


def cmd_ereshkigal_stats(args):
    """Show interception statistics."""
    init_db()

    stats = get_interception_stats(days=args.days)

    print(f"Ereshkigal Statistics (last {args.days} days)")
    print("=" * 40)
    print(f"Total attempts: {stats['total']}")
    print(f"Blocked: {stats['blocked']}")
    print(f"Allowed: {stats['allowed']}")
    if stats['total'] > 0:
        print(f"Block rate: {stats['blocked'] / stats['total'] * 100:.1f}%")
    print()

    if stats['by_category']:
        print("By category:")
        for cat, count in stats['by_category'].items():
            print(f"  {cat}: {count}")
        print()

    if stats['by_pattern']:
        print("Top patterns:")
        for pattern, count in list(stats['by_pattern'].items())[:5]:
            print(f"  {pattern}: {count}")
        print()

    if stats['false_positives'] > 0 or stats['legitimate_blocks'] > 0:
        total_evaluated = stats['false_positives'] + stats['legitimate_blocks']
        accuracy = stats['legitimate_blocks'] / total_evaluated * 100 if total_evaluated > 0 else 0
        print(f"False positives: {stats['false_positives']}")
        print(f"Pattern accuracy: {accuracy:.1f}%")


def cmd_ereshkigal_report(args):
    """Generate weekly report."""
    init_db()

    report = generate_weekly_report(days=args.days)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report)
        print(f"Report written to: {output_path}")
    else:
        print(report)


def cmd_ereshkigal_patterns(args):
    """List current patterns."""
    patterns = load_patterns()

    if args.json:
        print(json.dumps(patterns, indent=2))
    else:
        print("Ereshkigal Patterns")
        print("=" * 40)
        print(f"Version: {patterns.get('version', 'unknown')}")
        print(f"Updated: {patterns.get('updated_at', 'unknown')}")
        print(f"Updated by: {patterns.get('updated_by', 'unknown')}")
        print()

        categories = get_pattern_categories()
        for category in categories:
            pattern_list = patterns.get(category, [])
            print(f"{category} ({len(pattern_list)} patterns):")
            for p in pattern_list:
                print(f"  - {p}")
            print()


def cmd_ereshkigal_add(args):
    """Add a pattern."""
    add_pattern(args.pattern, args.category)
    print(f"Added pattern to {args.category}: {args.pattern}")


def cmd_ereshkigal_remove(args):
    """Remove a pattern."""
    if remove_pattern(args.pattern, args.category):
        print(f"Removed pattern from {args.category}: {args.pattern}")
    else:
        print(f"Pattern not found in {args.category}")
        sys.exit(1)


def cmd_ereshkigal_mark_fp(args):
    """Mark interception as false positive."""
    init_db()

    if mark_false_positive(args.interception_id, args.note):
        print(f"Marked as false positive: {args.interception_id}")
    else:
        print(f"Interception not found: {args.interception_id}")
        sys.exit(1)


def cmd_ereshkigal_mark_legit(args):
    """Mark interception as legitimate block."""
    init_db()

    if mark_legitimate(args.interception_id, args.note):
        print(f"Marked as legitimate: {args.interception_id}")
    else:
        print(f"Interception not found: {args.interception_id}")
        sys.exit(1)


def cmd_ereshkigal_recent(args):
    """Show recent interceptions."""
    init_db()

    interceptions = get_recent_interceptions(
        result=args.result,
        limit=args.limit,
    )

    if not interceptions:
        print("No interceptions found.")
        return

    for i in interceptions:
        status = "BLOCKED" if i['result'] == 'blocked' else "allowed"
        reasoning = i.get('reasoning', '')[:50]
        timestamp = i.get('timestamp', 'unknown')

        print(f"[{status}] {i['id'][:8]}")
        print(f"  Tool: {i.get('tool', 'unknown')}")
        print(f"  Reasoning: \"{reasoning}...\"")
        if i['result'] == 'blocked':
            print(f"  Pattern: {i.get('pattern', 'unknown')}")
        print(f"  Time: {timestamp}")
        if i.get('was_legitimate') is not None:
            legit = "Yes" if i['was_legitimate'] else "No (false positive)"
            print(f"  Legitimate: {legit}")
        print()


def cmd_ereshkigal_init(args):
    """Initialize patterns.json with default patterns."""
    path = init_patterns()
    print(f"Patterns initialized at: {path}")


# === Report Commands (Phase 8) ===

def cmd_report_weekly(args):
    """Generate weekly Ereshkigal report."""
    init_db()

    report = generate_weekly_report(days=args.days)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report)
        print(f"Report written to: {output_path}")
    elif args.summary:
        print(get_report_summary())
    else:
        print(report)


def cmd_report_evasions(args):
    """Show evasions that should have been blocked."""
    init_db()

    evasions = find_evasions_with_bugs(days=args.days)

    if not evasions:
        print("No evasions found (no allowed attempts correlated with later issues).")
        return

    print(f"Evasions (last {args.days} days)")
    print("=" * 50)
    print()

    for e in evasions:
        reasoning = e.get("reasoning", "")[:80]
        print(f"ID: {e['interception_id'][:8]}")
        print(f"Tool: {e.get('tool', 'unknown')}")
        print(f"Reasoning: \"{reasoning}...\"")
        print(f"Note: {e.get('correlation', 'Issues followed')}")
        print()


def cmd_report_prompt(args):
    """Generate prompt for fresh Claude analysis."""
    init_db()

    prompt = generate_fresh_claude_prompt(days=args.days)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(prompt)
        print(f"Prompt written to: {output_path}")
    else:
        print(prompt)


def cmd_report_checklist(args):
    """Generate human review checklist."""
    init_db()

    output_path = Path(args.output) if args.output else None
    checklist = generate_review_checklist(output_path)

    if output_path:
        print(f"Checklist written to: {output_path}")
    else:
        print(checklist)


def cmd_report_complete(args):
    """Mark review as complete."""
    complete_review()
    print("Review marked as complete.")
    print(f"Next review due in 7 days.")


def cmd_report_status(args):
    """Check if review is due."""
    init_db()

    reminder = get_review_reminder()

    if reminder:
        print(reminder)
    else:
        print("No review due. Pattern enforcement is up to date.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enki - Second brain for software engineering",
        prog="enki",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize Enki database")
    init_parser.set_defaults(func=cmd_init)

    # remember
    remember_parser = subparsers.add_parser("remember", help="Store a new bead")
    remember_parser.add_argument("content", help="Content to remember")
    remember_parser.add_argument(
        "-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"],
        default="learning",
        help="Type of knowledge",
    )
    remember_parser.add_argument("-s", "--summary", help="Short summary")
    remember_parser.add_argument("-p", "--project", help="Project identifier")
    remember_parser.add_argument("-c", "--context", help="Context when learned")
    remember_parser.add_argument("--tags", help="Comma-separated tags")
    remember_parser.add_argument("--starred", action="store_true", help="Star this bead")
    remember_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    remember_parser.set_defaults(func=cmd_remember)

    # recall
    recall_parser = subparsers.add_parser("recall", help="Search for beads")
    recall_parser.add_argument("query", help="Search query")
    recall_parser.add_argument("-p", "--project", help="Project filter")
    recall_parser.add_argument(
        "-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"],
        help="Type filter",
    )
    recall_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    recall_parser.set_defaults(func=cmd_recall)

    # status
    status_parser = subparsers.add_parser("status", help="Show memory status")
    status_parser.set_defaults(func=cmd_status)

    # recent
    recent_parser = subparsers.add_parser("recent", help="Show recent beads")
    recent_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    recent_parser.add_argument("-p", "--project", help="Project filter")
    recent_parser.set_defaults(func=cmd_recent)

    # star
    star_parser = subparsers.add_parser("star", help="Star a bead")
    star_parser.add_argument("bead_id", help="Bead ID to star")
    star_parser.set_defaults(func=cmd_star)

    # get
    get_parser = subparsers.add_parser("get", help="Get a specific bead")
    get_parser.add_argument("bead_id", help="Bead ID")
    get_parser.add_argument("--json", action="store_true", help="Output as JSON")
    get_parser.set_defaults(func=cmd_get)

    # maintain
    maintain_parser = subparsers.add_parser("maintain", help="Run maintenance tasks")
    maintain_parser.set_defaults(func=cmd_maintain)

    # === Session Commands ===
    session_parser = subparsers.add_parser("session", help="Session management")
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    # session start
    session_start_parser = session_subparsers.add_parser("start", help="Start a new session")
    session_start_parser.add_argument("-g", "--goal", help="Session goal")
    session_start_parser.add_argument("-p", "--project", help="Project path")
    session_start_parser.add_argument("--json", action="store_true", help="JSON output")
    session_start_parser.set_defaults(func=cmd_session_start)

    # session status
    session_status_parser = session_subparsers.add_parser("status", help="Show session status")
    session_status_parser.add_argument("-p", "--project", help="Project path")
    session_status_parser.add_argument("--json", action="store_true", help="JSON output")
    session_status_parser.set_defaults(func=cmd_session_status)

    # session set-phase
    session_phase_parser = session_subparsers.add_parser("set-phase", help="Set session phase")
    session_phase_parser.add_argument("phase", choices=["intake", "debate", "plan", "implement", "review", "test", "ship"])
    session_phase_parser.add_argument("-p", "--project", help="Project path")
    session_phase_parser.set_defaults(func=cmd_session_set_phase)

    # session set-goal
    session_goal_parser = session_subparsers.add_parser("set-goal", help="Set session goal")
    session_goal_parser.add_argument("goal", help="Session goal")
    session_goal_parser.add_argument("-p", "--project", help="Project path")
    session_goal_parser.set_defaults(func=cmd_session_set_goal)

    # session track-edit
    session_edit_parser = session_subparsers.add_parser("track-edit", help="Track a file edit")
    session_edit_parser.add_argument("--file", required=True, help="File path")
    session_edit_parser.add_argument("-p", "--project", help="Project path")
    session_edit_parser.set_defaults(func=cmd_session_track_edit)

    # === Gate Commands ===
    gate_parser = subparsers.add_parser("gate", help="Gate enforcement")
    gate_subparsers = gate_parser.add_subparsers(dest="gate_command")

    # gate check
    gate_check_parser = gate_subparsers.add_parser("check", help="Check if tool use is allowed")
    gate_check_parser.add_argument("--tool", required=True, help="Tool name")
    gate_check_parser.add_argument("--file", help="File path")
    gate_check_parser.add_argument("--agent", help="Agent type")
    gate_check_parser.add_argument("-p", "--project", help="Project path")
    gate_check_parser.add_argument("--json", action="store_true", help="JSON output")
    gate_check_parser.set_defaults(func=cmd_gate_check)

    # gate stats
    gate_stats_parser = gate_subparsers.add_parser("stats", help="Show violation statistics")
    gate_stats_parser.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    gate_stats_parser.set_defaults(func=cmd_gate_stats)

    # === PM Commands ===

    # debate
    debate_parser = subparsers.add_parser("debate", help="Start debate phase")
    debate_parser.add_argument("goal", nargs="?", help="Feature/change to debate")
    debate_parser.add_argument("-c", "--context", help="Additional context")
    debate_parser.add_argument("-p", "--project", help="Project path")
    debate_parser.add_argument("--check", action="store_true", help="Check if perspectives complete")
    debate_parser.set_defaults(func=lambda args: cmd_debate_check(args) if args.check else cmd_debate(args))

    # plan
    plan_parser = subparsers.add_parser("plan", help="Create spec from debate")
    plan_parser.add_argument("name", help="Spec name")
    plan_parser.add_argument("--problem", help="Problem statement")
    plan_parser.add_argument("--solution", help="Proposed solution")
    plan_parser.add_argument("-p", "--project", help="Project path")
    plan_parser.set_defaults(func=cmd_plan)

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a spec")
    approve_parser.add_argument("name", help="Spec name to approve")
    approve_parser.add_argument("-p", "--project", help="Project path")
    approve_parser.set_defaults(func=cmd_approve)

    # specs
    specs_parser = subparsers.add_parser("specs", help="List specs")
    specs_parser.add_argument("-p", "--project", help="Project path")
    specs_parser.add_argument("-v", "--verbose", action="store_true", help="Show paths")
    specs_parser.set_defaults(func=cmd_specs)

    # spec (show)
    spec_parser = subparsers.add_parser("spec", help="Show a spec")
    spec_parser.add_argument("name", help="Spec name")
    spec_parser.add_argument("-p", "--project", help="Project path")
    spec_parser.set_defaults(func=cmd_spec_show)

    # decompose
    decompose_parser = subparsers.add_parser("decompose", help="Decompose spec into tasks")
    decompose_parser.add_argument("name", help="Spec name")
    decompose_parser.add_argument("-p", "--project", help="Project path")
    decompose_parser.set_defaults(func=cmd_decompose)

    # orchestration status
    orch_parser = subparsers.add_parser("orchestration", help="Show orchestration status")
    orch_parser.add_argument("-p", "--project", help="Project path")
    orch_parser.add_argument("--json", action="store_true", help="JSON output")
    orch_parser.set_defaults(func=cmd_orchestration_status)

    # orchestrate (start)
    orchestrate_parser = subparsers.add_parser("orchestrate", help="Start orchestration")
    orchestrate_parser.add_argument("name", help="Spec name to orchestrate")
    orchestrate_parser.add_argument("-p", "--project", help="Project path")
    orchestrate_parser.set_defaults(func=cmd_orchestrate_start)

    # === Task Commands ===
    task_parser = subparsers.add_parser("task", help="Task management")
    task_subparsers = task_parser.add_subparsers(dest="task_command")

    # task start
    task_start_parser = task_subparsers.add_parser("start", help="Start a task")
    task_start_parser.add_argument("task_id", help="Task ID")
    task_start_parser.add_argument("-p", "--project", help="Project path")
    task_start_parser.set_defaults(func=cmd_task_start)

    # task complete
    task_complete_parser = task_subparsers.add_parser("complete", help="Complete a task")
    task_complete_parser.add_argument("task_id", help="Task ID")
    task_complete_parser.add_argument("-o", "--output", help="Task output")
    task_complete_parser.add_argument("-p", "--project", help="Project path")
    task_complete_parser.set_defaults(func=cmd_task_complete)

    # task fail
    task_fail_parser = task_subparsers.add_parser("fail", help="Mark task as failed")
    task_fail_parser.add_argument("task_id", help="Task ID")
    task_fail_parser.add_argument("-r", "--reason", help="Failure reason")
    task_fail_parser.add_argument("-p", "--project", help="Project path")
    task_fail_parser.set_defaults(func=cmd_task_fail)

    # === Bug Commands ===
    bug_parser = subparsers.add_parser("bug", help="Bug management")
    bug_subparsers = bug_parser.add_subparsers(dest="bug_command")

    # bug file
    bug_file_parser = bug_subparsers.add_parser("file", help="File a new bug")
    bug_file_parser.add_argument("title", help="Bug title")
    bug_file_parser.add_argument("-d", "--description", help="Bug description")
    bug_file_parser.add_argument("-f", "--found-by", default="QA", help="Agent that found it")
    bug_file_parser.add_argument("-s", "--severity", choices=["critical", "high", "medium", "low"], default="medium")
    bug_file_parser.add_argument("-t", "--task", help="Related task ID")
    bug_file_parser.add_argument("-p", "--project", help="Project path")
    bug_file_parser.set_defaults(func=cmd_bug_file)

    # bug list
    bug_list_parser = bug_subparsers.add_parser("list", help="List bugs")
    bug_list_parser.add_argument("-p", "--project", help="Project path")
    bug_list_parser.set_defaults(func=cmd_bug_list)

    # bug close
    bug_close_parser = bug_subparsers.add_parser("close", help="Close a bug")
    bug_close_parser.add_argument("bug_id", help="Bug ID")
    bug_close_parser.add_argument("-r", "--resolution", choices=["fixed", "wontfix"], default="fixed")
    bug_close_parser.add_argument("-p", "--project", help="Project path")
    bug_close_parser.set_defaults(func=cmd_bug_close)

    # bug reopen
    bug_reopen_parser = bug_subparsers.add_parser("reopen", help="Reopen a bug")
    bug_reopen_parser.add_argument("bug_id", help="Bug ID")
    bug_reopen_parser.add_argument("-p", "--project", help="Project path")
    bug_reopen_parser.set_defaults(func=cmd_bug_reopen)

    # === HITL Commands ===
    hitl_parser = subparsers.add_parser("hitl", help="HITL management")
    hitl_subparsers = hitl_parser.add_subparsers(dest="hitl_command")

    # hitl status
    hitl_status_parser = hitl_subparsers.add_parser("status", help="Check HITL status")
    hitl_status_parser.add_argument("-p", "--project", help="Project path")
    hitl_status_parser.set_defaults(func=cmd_hitl_status)

    # hitl resolve
    hitl_resolve_parser = hitl_subparsers.add_parser("resolve", help="Resolve HITL")
    hitl_resolve_parser.add_argument("-r", "--reason", required=True, help="Resolution reason")
    hitl_resolve_parser.add_argument("-p", "--project", help="Project path")
    hitl_resolve_parser.set_defaults(func=cmd_hitl_resolve)

    # next
    next_parser = subparsers.add_parser("next", help="Show next recommended action")
    next_parser.add_argument("-p", "--project", help="Project path")
    next_parser.add_argument("--json", action="store_true", help="JSON output")
    next_parser.set_defaults(func=cmd_next)

    # agents
    agents_parser = subparsers.add_parser("agents", help="List available agents")
    agents_parser.set_defaults(func=cmd_agents)

    # === Persona Commands ===

    # context
    context_parser = subparsers.add_parser("context", help="Show Enki's context injection")
    context_parser.add_argument("-p", "--project", help="Project path")
    context_parser.set_defaults(func=cmd_context)

    # greeting
    greeting_parser = subparsers.add_parser("greeting", help="Show Enki's greeting")
    greeting_parser.add_argument("-p", "--project", help="Project path")
    greeting_parser.set_defaults(func=cmd_greeting)

    # summary
    summary_parser = subparsers.add_parser("summary", help="Show session summary")
    summary_parser.add_argument("-p", "--project", help="Project path")
    summary_parser.set_defaults(func=cmd_summary)

    # error-context
    error_context_parser = subparsers.add_parser("error-context", help="Show context for an error")
    error_context_parser.add_argument("error", help="Error text")
    error_context_parser.add_argument("-p", "--project", help="Project path")
    error_context_parser.set_defaults(func=cmd_error_context)

    # decision-context
    decision_context_parser = subparsers.add_parser("decision-context", help="Show context for a decision")
    decision_context_parser.add_argument("topic", help="Decision topic")
    decision_context_parser.add_argument("-p", "--project", help="Project path")
    decision_context_parser.set_defaults(func=cmd_decision_context)

    # === Evolution Commands ===
    evolution_parser = subparsers.add_parser("evolution", help="Self-evolution management")
    evolution_subparsers = evolution_parser.add_subparsers(dest="evolution_command")

    # evolution summary
    evolution_summary_parser = evolution_subparsers.add_parser("summary", help="Show evolution summary")
    evolution_summary_parser.add_argument("-p", "--project", help="Project path")
    evolution_summary_parser.set_defaults(func=cmd_evolution_summary)

    # evolution patterns
    evolution_patterns_parser = evolution_subparsers.add_parser("patterns", help="Show violation patterns")
    evolution_patterns_parser.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    evolution_patterns_parser.add_argument("-p", "--project", help="Project path")
    evolution_patterns_parser.set_defaults(func=cmd_evolution_patterns)

    # evolution triggers
    evolution_triggers_parser = evolution_subparsers.add_parser("triggers", help="Check correction triggers")
    evolution_triggers_parser.add_argument("-p", "--project", help="Project path")
    evolution_triggers_parser.set_defaults(func=cmd_evolution_triggers)

    # evolution review
    evolution_review_parser = evolution_subparsers.add_parser("review", help="Run weekly self-review")
    evolution_review_parser.add_argument("-p", "--project", help="Project path")
    evolution_review_parser.set_defaults(func=cmd_evolution_review)

    # evolution explain
    evolution_explain_parser = evolution_subparsers.add_parser("explain", help="Explain a blocking decision")
    evolution_explain_parser.add_argument("gate", help="Gate that blocked")
    evolution_explain_parser.add_argument("-r", "--reason", help="Block reason")
    evolution_explain_parser.add_argument("-p", "--project", help="Project path")
    evolution_explain_parser.set_defaults(func=cmd_evolution_explain)

    # evolution ask
    evolution_ask_parser = evolution_subparsers.add_parser("ask", help="Ask about Enki's behavior")
    evolution_ask_parser.add_argument("question", help="Your question")
    evolution_ask_parser.add_argument("-p", "--project", help="Project path")
    evolution_ask_parser.set_defaults(func=cmd_evolution_ask)

    # evolution status
    evolution_status_parser = evolution_subparsers.add_parser("status", help="Check if review is due")
    evolution_status_parser.add_argument("-p", "--project", help="Project path")
    evolution_status_parser.set_defaults(func=cmd_evolution_status)

    # === Ereshkigal Commands ===
    ereshkigal_parser = subparsers.add_parser("ereshkigal", help="Pattern interceptor (Ereshkigal)")
    ereshkigal_subparsers = ereshkigal_parser.add_subparsers(dest="ereshkigal_command")

    # ereshkigal init
    ereshkigal_init_parser = ereshkigal_subparsers.add_parser("init", help="Initialize patterns.json")
    ereshkigal_init_parser.set_defaults(func=cmd_ereshkigal_init)

    # ereshkigal test
    ereshkigal_test_parser = ereshkigal_subparsers.add_parser("test", help="Test if reasoning would be blocked")
    ereshkigal_test_parser.add_argument("reasoning", help="Reasoning text to test")
    ereshkigal_test_parser.set_defaults(func=cmd_ereshkigal_test)

    # ereshkigal intercept
    ereshkigal_intercept_parser = ereshkigal_subparsers.add_parser("intercept", help="Run interception")
    ereshkigal_intercept_parser.add_argument("--tool", required=True, help="Tool being used")
    ereshkigal_intercept_parser.add_argument("--reasoning", required=True, help="Claude's reasoning")
    ereshkigal_intercept_parser.add_argument("--session", help="Session ID")
    ereshkigal_intercept_parser.add_argument("--phase", help="Current phase")
    ereshkigal_intercept_parser.add_argument("--json", action="store_true", help="JSON output")
    ereshkigal_intercept_parser.set_defaults(func=cmd_ereshkigal_intercept)

    # ereshkigal stats
    ereshkigal_stats_parser = ereshkigal_subparsers.add_parser("stats", help="Show interception statistics")
    ereshkigal_stats_parser.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    ereshkigal_stats_parser.set_defaults(func=cmd_ereshkigal_stats)

    # ereshkigal report
    ereshkigal_report_parser = ereshkigal_subparsers.add_parser("report", help="Generate weekly report")
    ereshkigal_report_parser.add_argument("-d", "--days", type=int, default=7, help="Days to include")
    ereshkigal_report_parser.add_argument("-o", "--output", help="Output file path")
    ereshkigal_report_parser.set_defaults(func=cmd_ereshkigal_report)

    # ereshkigal patterns
    ereshkigal_patterns_parser = ereshkigal_subparsers.add_parser("patterns", help="List current patterns")
    ereshkigal_patterns_parser.add_argument("--json", action="store_true", help="JSON output")
    ereshkigal_patterns_parser.set_defaults(func=cmd_ereshkigal_patterns)

    # ereshkigal add
    ereshkigal_add_parser = ereshkigal_subparsers.add_parser("add", help="Add a pattern")
    ereshkigal_add_parser.add_argument("pattern", help="Regex pattern to add")
    ereshkigal_add_parser.add_argument("-c", "--category", required=True,
                                        choices=["skip_patterns", "minimize_patterns",
                                                "urgency_patterns", "certainty_patterns"],
                                        help="Pattern category")
    ereshkigal_add_parser.set_defaults(func=cmd_ereshkigal_add)

    # ereshkigal remove
    ereshkigal_remove_parser = ereshkigal_subparsers.add_parser("remove", help="Remove a pattern")
    ereshkigal_remove_parser.add_argument("pattern", help="Pattern to remove")
    ereshkigal_remove_parser.add_argument("-c", "--category", required=True, help="Pattern category")
    ereshkigal_remove_parser.set_defaults(func=cmd_ereshkigal_remove)

    # ereshkigal mark-fp
    ereshkigal_fp_parser = ereshkigal_subparsers.add_parser("mark-fp", help="Mark as false positive")
    ereshkigal_fp_parser.add_argument("interception_id", help="Interception ID")
    ereshkigal_fp_parser.add_argument("-n", "--note", help="Note explaining why")
    ereshkigal_fp_parser.set_defaults(func=cmd_ereshkigal_mark_fp)

    # ereshkigal mark-legit
    ereshkigal_legit_parser = ereshkigal_subparsers.add_parser("mark-legit", help="Mark as legitimate block")
    ereshkigal_legit_parser.add_argument("interception_id", help="Interception ID")
    ereshkigal_legit_parser.add_argument("-n", "--note", help="Outcome note")
    ereshkigal_legit_parser.set_defaults(func=cmd_ereshkigal_mark_legit)

    # ereshkigal recent
    ereshkigal_recent_parser = ereshkigal_subparsers.add_parser("recent", help="Show recent interceptions")
    ereshkigal_recent_parser.add_argument("-r", "--result", choices=["allowed", "blocked"], help="Filter by result")
    ereshkigal_recent_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    ereshkigal_recent_parser.set_defaults(func=cmd_ereshkigal_recent)

    # === Report Commands (Phase 8) ===
    report_parser = subparsers.add_parser("report", help="Pattern evolution reports")
    report_subparsers = report_parser.add_subparsers(dest="report_command")

    # report weekly
    report_weekly_parser = report_subparsers.add_parser("weekly", help="Generate weekly report")
    report_weekly_parser.add_argument("-d", "--days", type=int, default=7, help="Days to include")
    report_weekly_parser.add_argument("-o", "--output", help="Output file path")
    report_weekly_parser.add_argument("--summary", action="store_true", help="One-line summary only")
    report_weekly_parser.set_defaults(func=cmd_report_weekly)

    # report evasions
    report_evasions_parser = report_subparsers.add_parser("evasions", help="Show evasions that caused issues")
    report_evasions_parser.add_argument("-d", "--days", type=int, default=30, help="Days to look back")
    report_evasions_parser.set_defaults(func=cmd_report_evasions)

    # report prompt
    report_prompt_parser = report_subparsers.add_parser("prompt", help="Generate fresh Claude analysis prompt")
    report_prompt_parser.add_argument("-d", "--days", type=int, default=7, help="Days of data to include")
    report_prompt_parser.add_argument("-o", "--output", help="Output file path")
    report_prompt_parser.set_defaults(func=cmd_report_prompt)

    # report checklist
    report_checklist_parser = report_subparsers.add_parser("checklist", help="Generate review checklist")
    report_checklist_parser.add_argument("-o", "--output", help="Output file path")
    report_checklist_parser.set_defaults(func=cmd_report_checklist)

    # report complete
    report_complete_parser = report_subparsers.add_parser("complete", help="Mark review as complete")
    report_complete_parser.set_defaults(func=cmd_report_complete)

    # report status
    report_status_parser = report_subparsers.add_parser("status", help="Check if review is due")
    report_status_parser.set_defaults(func=cmd_report_status)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
