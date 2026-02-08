#!/bin/bash
set -euo pipefail
# Enki Pre-Tool-Use Hook
# Called before each tool execution in Claude Code
#
# Input: JSON via stdin with tool_name and tool_input
# Output: JSON with decision (allow/block) and optional reason
#
# Two-layer enforcement:
# 1. Gate checks (phase, spec, TDD, scope)
# 2. Ereshkigal pattern interception (reasoning analysis)

# P1-12: Fail-closed — require jq
if ! command -v jq &> /dev/null; then
    echo '{"decision": "block", "reason": "Required dependency jq not found — fail closed"}'
    exit 0
fi

# Read input from stdin
INPUT=$(cat)

# Extract tool name, file path, and reasoning (P1-06: all vars quoted with ${})
TOOL=$(echo "${INPUT}" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // ""')
AGENT_TYPE=$(echo "${INPUT}" | jq -r '.tool_input.subagent_type // ""')
CWD=$(echo "${INPUT}" | jq -r '.cwd // "."')
SESSION_ID=$(echo "${INPUT}" | jq -r '.session_id // ""')

# === Reasoning extraction per tool type (Table 9, Hardening Spec v2) ===
# Each tool type has a primary and fallback reasoning source.
REASONING=""
case "${TOOL}" in
    Bash)
        REASONING=$(echo "${INPUT}" | jq -r '.tool_input.description // .reasoning // ""')
        ;;
    Edit)
        REASONING=$(echo "${INPUT}" | jq -r '.tool_input.description // (.tool_input.old_string // "" | .[0:100]) // ""')
        ;;
    Write)
        REASONING=$(echo "${INPUT}" | jq -r '.tool_input.description // .tool_input.file_path // ""')
        ;;
    MultiEdit)
        REASONING=$(echo "${INPUT}" | jq -r '.tool_input.description // .tool_input.file_path // ""')
        ;;
    Task)
        REASONING=$(echo "${INPUT}" | jq -r '.tool_input.description // (.tool_input.prompt // "" | .[0:200]) // ""')
        ;;
    *)
        # Non-Table-9 tools: no reasoning extraction needed
        REASONING=""
        ;;
esac

# Skip if no tool name
if [[ -z "${TOOL}" ]]; then
    echo '{"decision": "allow"}'
    exit 0
fi

# P1-11: Discover enki binary dynamically instead of hardcoded path
ENKI_BIN="${ENKI_BIN:-}"
if [[ -z "${ENKI_BIN}" ]]; then
    # Try CWD venv first, then PATH
    if [[ -x "${CWD}/.venv/bin/enki" ]]; then
        ENKI_BIN="${CWD}/.venv/bin/enki"
    elif command -v enki &> /dev/null; then
        ENKI_BIN="$(command -v enki)"
    fi
fi

# P1-12: Fail-closed if enki is not available
if [[ -z "${ENKI_BIN}" ]] || [[ ! -x "${ENKI_BIN}" ]]; then
    echo '{"decision": "block", "reason": "Enki CLI not found. Cannot verify gates — fail closed."}'
    exit 0
fi

# === Layer 0: Infrastructure Blocklist (Hardening Spec v2, Step 1.5) ===
# Shell-layer hard stop. No exceptions. No scope override. No phase override.
# These files are NEVER writable by any tool, regardless of context.
INFRA_BLOCKLIST="enforcement.py ereshkigal.py hooks.py enki-pre-tool-use.sh enki-post-tool-use.sh patterns.json"

if [[ "${TOOL}" =~ ^(Edit|Write|MultiEdit)$ ]] && [[ -n "${FILE_PATH}" ]]; then
    BASENAME=$(basename "${FILE_PATH}")
    for BLOCKED in ${INFRA_BLOCKLIST}; do
        if [[ "${BASENAME}" == "${BLOCKED}" ]]; then
            echo "{\"decision\": \"block\", \"reason\": \"INFRASTRUCTURE BLOCKLIST: ${BASENAME} is never writable. This is a shell-layer hard stop — no exceptions.\"}"
            exit 0
        fi
    done
fi

# === Layer 1: Gate Checks ===
# Check phase, spec, TDD, scope gates
GATE_RESULT=$("${ENKI_BIN}" gate check \
    --tool "${TOOL}" \
    --file "${FILE_PATH}" \
    --agent "${AGENT_TYPE}" \
    --project "${CWD}" \
    --json 2>/dev/null) || true

if [[ -n "${GATE_RESULT}" ]]; then
    GATE_DECISION=$(echo "${GATE_RESULT}" | jq -r '.decision // "block"')

    if [[ "${GATE_DECISION}" == "block" ]]; then
        echo "${GATE_RESULT}"
        exit 0
    fi
else
    # P1-12: Empty gate result = fail closed
    echo '{"decision": "block", "reason": "Gate check returned empty result — fail closed"}'
    exit 0
fi

# === Layer 2: Ereshkigal Pattern Interception ===
# Table 9 (Hardening Spec v2): Ereshkigal intercepts ALL state-modifying tools.
# Bash, Edit, Write, MultiEdit, Task — no exceptions.
NEEDS_ERESHKIGAL=false

if [[ "${TOOL}" =~ ^(Bash|Edit|Write|MultiEdit|Task)$ ]]; then
    NEEDS_ERESHKIGAL=true
fi

if [[ "${NEEDS_ERESHKIGAL}" == "true" ]]; then
    # Fail-closed: Table 9 tools MUST provide reasoning for Ereshkigal analysis
    if [[ -z "${REASONING}" ]]; then
        echo '{"decision": "block", "reason": "No reasoning provided. Ereshkigal requires justification for all state-modifying tools (Table 9)."}'
        exit 0
    fi

    ERESHKIGAL_RESULT=$("${ENKI_BIN}" ereshkigal intercept \
        --tool "${TOOL}" \
        --reasoning "${REASONING}" \
        --session "${SESSION_ID}" \
        --phase "$(cat "${CWD}/.enki/PHASE" 2>/dev/null || echo 'unknown')" \
        --json 2>/dev/null) || true

    if [[ -n "${ERESHKIGAL_RESULT}" ]]; then
        ERESHKIGAL_DECISION=$(echo "${ERESHKIGAL_RESULT}" | jq -r '.allowed // true')

        if [[ "${ERESHKIGAL_DECISION}" == "false" ]]; then
            REASON=$(echo "${ERESHKIGAL_RESULT}" | jq -r '.message // "Blocked by Ereshkigal"')
            echo "{\"decision\": \"block\", \"reason\": $(echo "${REASON}" | jq -Rs .)}"
            exit 0
        fi
    fi
fi

# All checks passed
echo '{"decision": "allow"}'
