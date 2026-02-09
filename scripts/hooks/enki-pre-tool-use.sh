#!/bin/bash
HOOK_VERSION="2026-02-10-v3"
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
INFRA_BLOCKLIST="enforcement.py ereshkigal.py hooks.py enki-pre-tool-use.sh enki-post-tool-use.sh patterns.json gates.py"

if [[ "${TOOL}" =~ ^(Edit|Write|MultiEdit)$ ]] && [[ -n "${FILE_PATH}" ]]; then
    BASENAME=$(basename "${FILE_PATH}")
    for BLOCKED in ${INFRA_BLOCKLIST}; do
        if [[ "${BASENAME}" == "${BLOCKED}" ]]; then
            echo "{\"decision\": \"block\", \"reason\": \"INFRASTRUCTURE BLOCKLIST: ${BASENAME} is never writable. This is a shell-layer hard stop — no exceptions.\"}"
            exit 0
        fi
    done
fi

# === Layer 0b: Bash Command Inspection (Protected File Guard) ===
# Bash can modify files via sed, tee, echo>, etc. — bypassing Edit/Write gates.
# Inspect the actual command content against the same infrastructure blocklist.
if [[ "${TOOL}" == "Bash" ]]; then
    COMMAND=$(echo "${INPUT}" | jq -r '.tool_input.command // ""')

    # Git operations are safe — version control, not file mutation
    if echo "${COMMAND}" | grep -qE "^git "; then
        true  # allow git through Layer 0b
    else
        for BLOCKED in ${INFRA_BLOCKLIST}; do
            if echo "${COMMAND}" | grep -q "${BLOCKED}"; then
                if echo "${COMMAND}" | grep -qE "sed -i|tee |> |>> |cat >|echo >|cp |mv |rm |chmod |perl -pi|awk.*>|python.*open|dd |truncate|install "; then
                    echo "{\"decision\": \"block\", \"reason\": \"INFRASTRUCTURE BLOCKLIST: Bash command targets protected file ${BLOCKED}. Shell-layer hard stop — no exceptions.\"}"
                    exit 0
                fi
            fi
        done

        if echo "${COMMAND}" | grep -qE "base64.*-d|eval |source /tmp|bash /tmp|\\\$\(.*\).*>.*\.(py|sh|json)"; then
            echo '{"decision": "block", "reason": "Indirect file modification pattern detected — fail closed."}'
            exit 0
        fi
    fi
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

# === Layer 1.5: File Claim Check (Spec 3: Agent Messaging) ===
# Block Edit/Write/MultiEdit on files claimed by another agent.
# No bypass flag (AM-2). Uses Python to query the DB directly.
ENKI_DIR="${CWD}/.enki"

if [[ "${TOOL}" =~ ^(Edit|Write|MultiEdit)$ ]] && [[ -n "${FILE_PATH}" ]]; then
    PYTHON=""
    for candidate in \
        "${CWD}/.venv/bin/python" \
        "${CWD}/.venv/bin/python3" \
        "$(which python3 2>/dev/null || true)" \
        "$(which python 2>/dev/null || true)"; do
        if [[ -n "${candidate}" ]] && [[ -x "${candidate}" ]]; then
            PYTHON="${candidate}"
            break
        fi
    done

    if [[ -n "${PYTHON}" ]]; then
        CURRENT_AGENT=$(cat "${ENKI_DIR}/.current_agent" 2>/dev/null || echo "")
        CLAIM_OWNER=$( ENKI_CWD="${CWD}" ENKI_FILE="${FILE_PATH}" ENKI_SESSION="${SESSION_ID}" \
            timeout 2 "${PYTHON}" -c "
import os, sys, sqlite3
cwd = os.environ.get('ENKI_CWD', '.')
db_path = os.path.join(cwd, '.enki', 'wisdom.db')
if not os.path.exists(db_path):
    sys.exit(0)
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute(
    'SELECT agent_id FROM file_claims WHERE file_path = ? AND session_id = ? AND released_at IS NULL',
    (os.environ['ENKI_FILE'], os.environ['ENKI_SESSION'])
).fetchone()
if row:
    print(row['agent_id'])
conn.close()
" 2>/dev/null ) || true

        if [[ -n "${CLAIM_OWNER}" ]] && [[ "${CLAIM_OWNER}" != "${CURRENT_AGENT}" ]]; then
            BLOCK_REASON="FILE CLAIM CONFLICT: ${FILE_PATH} is claimed by ${CLAIM_OWNER}. Wait for release or send a message requesting access."
            echo "{\"decision\": \"block\", \"reason\": $(echo "${BLOCK_REASON}" | jq -Rs .)}"
            exit 0
        fi
    fi
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
# === Ereshkigal fail-closed guard ===
    # If Ereshkigal crashes/OOMs/times out, result is empty.
    # Gate check blocks on empty (line 109); Ereshkigal must do the same.
    if [[ -z "${ERESHKIGAL_RESULT}" ]]; then
        echo '{"decision": "block", "reason": "Ereshkigal returned empty — fail closed. No state-modifying tools without interception check."}'
        exit 0
    fi

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
