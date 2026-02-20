#!/bin/bash
# HOOK_VERSION=v4.0.1
# hooks/session-end.sh — Finalize Uru + Abzu, generate proposals
set -euo pipefail

INPUT=$(cat)

# Uru: End enforcement session
RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook session-end 2>/dev/null || true)

# Uru: Generate feedback proposals
python -c "
import sys
sys.path.insert(0, 'src')
from enki.gates.uru import _get_session_id
from enki.gates.feedback import generate_session_proposals
session_id = _get_session_id()
proposals = generate_session_proposals(session_id)
if proposals:
    print(f'Generated {len(proposals)} feedback proposal(s)')
" 2>/dev/null || true

# Abzu: Finalize session (reconcile summaries, extract candidates, run decay)
python -c "
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
" 2>/dev/null || true

if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    echo '{"decision":"allow"}'
fi
