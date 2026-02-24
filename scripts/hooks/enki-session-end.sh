#!/bin/bash
# HOOK_VERSION=v4.0.1
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
    (echo "" >> "$LOG") 2>/dev/null || true
fi
# hooks/session-end.sh — Finalize Uru + Abzu, generate proposals
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Uru: End enforcement session
echo "$(date -Iseconds) [enki-session-end] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook session-end 2>>"$LOG" || true)

# Uru: Generate feedback proposals
echo "$(date -Iseconds) [enki-session-end] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from enki.gates.uru import _get_session_id
from enki.gates.feedback import generate_session_proposals
session_id = _get_session_id()
proposals = generate_session_proposals(session_id)
if proposals:
    print(f'Generated {len(proposals)} feedback proposal(s)')
" 2>>"$LOG" || true

# Abzu: Finalize session (reconcile summaries, extract candidates, run decay)
echo "$(date -Iseconds) [enki-session-end] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
try:
    from enki.memory.abzu import finalize_session
    from enki.gates.uru import _get_session_id
    session_id = _get_session_id()
    if session_id:
        result = finalize_session(session_id=session_id, project=None)
        if result:
            candidates = result.get('candidates_extracted', 0)
            if candidates:
                print(f'Extracted {candidates} bead candidate(s)')
except Exception:
    pass
" 2>>"$LOG" || true

if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    echo "$(date -Iseconds) [enki-session-end] EMPTY RESULT tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
    echo '{"decision":"allow"}'
fi
