#!/bin/bash
set -euo pipefail
# Enki Session End Hook
# Called when Claude Code session ends (Stop hook)
#
# Runs: enki session end — reflect + feedback loop + archive + summary table
# This is the counterpart to enki-session-start.sh

# Read input from stdin (Claude Code passes JSON with cwd, etc.)
INPUT=$(cat)

CWD=$(echo "${INPUT}" | jq -r '.cwd // "."')

# P1-11: Discover enki binary dynamically
ENKI_BIN="${ENKI_BIN:-}"
if [[ -z "${ENKI_BIN}" ]]; then
    if [[ -x "${CWD}/.venv/bin/enki" ]]; then
        ENKI_BIN="${CWD}/.venv/bin/enki"
    elif command -v enki &> /dev/null; then
        ENKI_BIN="$(command -v enki)"
    fi
fi

if [[ -z "${ENKI_BIN}" ]] || [[ ! -x "${ENKI_BIN}" ]]; then
    exit 0
fi

# Check if there's an active session
if [[ ! -f "${CWD}/.enki/SESSION_ID" ]]; then
    exit 0
fi

# =============================================================================
# RUN SESSION END (reflect + feedback loop + archive + summary)
# =============================================================================

"${ENKI_BIN}" session end --project "${CWD}" 2>&1

# =============================================================================
# MAINTENANCE (decay weights — lightweight, still useful)
# =============================================================================

"${ENKI_BIN}" maintain 2>/dev/null || true

exit 0
