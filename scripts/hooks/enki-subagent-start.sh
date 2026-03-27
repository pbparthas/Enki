#!/bin/bash
# HOOK_VERSION=v4.1.0
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
set -euo pipefail

INPUT=$(cat)

# Read current session ID
SESSION_ID=""
SESSION_FILE="$HOME/.enki/current_session_id"
if [[ -f "$SESSION_FILE" ]]; then
    SESSION_ID=$(cat "$SESSION_FILE" 2>/dev/null || true)
fi

# Build minimal governance context for subagent
CONTEXT=$(/home/partha/.enki-venv/bin/python -c "
import os
import sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')

try:
    from enki.project_state import resolve_project_from_cwd, read_project_state

    project = resolve_project_from_cwd(os.getcwd()) or 'unknown'
    phase = read_project_state(project, 'phase') or 'unknown'
    goal = read_project_state(project, 'goal') or ''
    session_id = '${SESSION_ID}'

    lines = [
        '## Enki Subagent Context',
        f'Project: {project} | Phase: {phase}',
        f'Session: {session_id}',
        '',
        'You are an Enki subagent. Governance rules:',
        '- Follow your role prompt from ~/.enki/prompts/{{role}}.md verbatim',
        '- Output valid JSON per _base.md schema — no preamble, no markdown wrapper',
        '- Do NOT call enki_* MCP tools — EM session handles all state',
        '- Scope lock: only modify files explicitly assigned to your task',
        '- If blocked: set status=BLOCKED in your JSON output, explain in blockers array',
    ]
    if goal:
        lines.insert(2, f'Goal: {goal[:100]}')

    print('\\n'.join(lines))
except Exception as e:
    print(f'Enki subagent context unavailable: {e}')
" 2>>"$LOG" || echo "Enki subagent context unavailable.")

# Output as JSON context injection
CONTEXT_JSON=$(/home/partha/.enki-venv/bin/python -c "
import json
import sys
content = sys.stdin.read()
print(json.dumps({'decision': 'allow', 'context': content}))
" <<< "$CONTEXT" 2>/dev/null || echo '{"decision": "allow"}')

echo "$CONTEXT_JSON"
