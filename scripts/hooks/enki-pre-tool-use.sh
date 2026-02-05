
#!/bin/bash
# Enki Pre-Tool-Use Hook
# Called before each tool execution in Claude Code
#
# Input: JSON via stdin with tool_name and tool_input
# Output: JSON with decision (allow/block) and optional reason
#

# Two-layer enforcement:
# 1. Gate checks (phase, spec, TDD, scope)
# 2. Ereshkigal pattern interception (reasoning analysis)


# Read input from stdin
INPUT=$(cat)


# DEBUG: Log raw input to see what fields are available
echo "$(date): $INPUT" >> /tmp/enki-hook-debug.log

# Extract tool name, file path, and reasoning
TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
AGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

# Extract reasoning from the conversation context (if available)
# This captures Claude's explanation for the action
REASONING=$(echo "$INPUT" | jq -r '.tool_input.description // .reasoning // ""')

# Skip if no tool name
if [[ -z "$TOOL" ]]; then
    echo '{"decision": "allow"}'
    exit 0
fi

# Check if enki CLI is available
if ! command -v /home/partha/Desktop/Enki/.venv/bin/enki &> /dev/null; then
    echo '{"decision": "block", "reason": "Enki not installed. Cannot verify gates."}'
    exit 0
fi

# === Layer 1: Gate Checks ===
# Check phase, spec, TDD, scope gates
GATE_RESULT=$(/home/partha/Desktop/Enki/.venv/bin/enki gate check \
    --tool "$TOOL" \
    --file "$FILE_PATH" \
    --agent "$AGENT_TYPE" \
    --project "$CWD" \
    --json 2>/dev/null)

if [[ $? -eq 0 ]] && [[ -n "$GATE_RESULT" ]]; then
    GATE_DECISION=$(echo "$GATE_RESULT" | jq -r '.decision // "allow"')

    if [[ "$GATE_DECISION" == "block" ]]; then
        # Gate blocked - return immediately
        echo "$GATE_RESULT"
        exit 0
    fi
fi

# === Layer 2: Ereshkigal Pattern Interception ===
# Bash always needs reasoning check.
# Edit/Write to enforcement paths also need reasoning check (Fix 6).
NEEDS_ERESHKIGAL=false

if [[ "$TOOL" == "Bash" ]]; then
    NEEDS_ERESHKIGAL=true
fi

# Edit/Write to enforcement infrastructure paths
if [[ "$TOOL" =~ ^(Edit|Write|MultiEdit)$ ]] && [[ -n "$FILE_PATH" ]]; then
    if echo "$FILE_PATH" | grep -qiE "(enki|enforcement|ereshkigal|evolution|hooks|patterns\.json|\.claude/hooks)"; then
        NEEDS_ERESHKIGAL=true
    fi
fi

if [[ "$NEEDS_ERESHKIGAL" == "true" ]]; then
    if [[ -z "$REASONING" ]]; then
        echo '{"decision": "block", "reason": "No reasoning provided. Ereshkigal requires justification."}'
        exit 0
    fi

    ERESHKIGAL_RESULT=$(/home/partha/Desktop/Enki/.venv/bin/enki ereshkigal intercept \
        --tool "$TOOL" \
        --reasoning "$REASONING" \
        --session "$SESSION_ID" \
        --phase "$(cat "$CWD/.enki/PHASE" 2>/dev/null || echo 'unknown')" \
        --json 2>/dev/null)

    if [[ $? -eq 0 ]] && [[ -n "$ERESHKIGAL_RESULT" ]]; then
        ERESHKIGAL_DECISION=$(echo "$ERESHKIGAL_RESULT" | jq -r '.allowed // true')

        if [[ "$ERESHKIGAL_DECISION" == "false" ]]; then
            REASON=$(echo "$ERESHKIGAL_RESULT" | jq -r '.message // "Blocked by Ereshkigal"')
            echo "{\"decision\": \"block\", \"reason\": $(echo "$REASON" | jq -Rs .)}"
            exit 0
        fi
    fi
fi

# All checks passed
echo '{"decision": "allow"}'
