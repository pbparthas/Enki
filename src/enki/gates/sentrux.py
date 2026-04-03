"""Sentrux — session-level drift scoring.

Runs in PostToolUse hook. Scores each tool call against drift patterns.
Cumulative score tracked per session in uru.db.
Nudges at NUDGE_THRESHOLD, escalates at ESCALATE_THRESHOLD.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NUDGE_THRESHOLD = 8.0
ESCALATE_THRESHOLD = 20.0
ENKI_ROOT = Path(os.environ.get("ENKI_ROOT", Path.home() / ".enki"))
DRIFT_PATTERNS_PATH = ENKI_ROOT / "drift-patterns.json"

DEFAULT_DRIFT_PATTERNS = {
    "force_flag_use": {
        "weight": 3.0,
        "description": "Explicit bypass of governance gates via force=True or skip_*=True",
        "signals": ["force=True", "skip_council=True", "skip_validation=True"],
        "match_type": "tool_input_contains",
    },
    "report_without_task_tool": {
        "weight": 5.0,
        "description": "enki_report called in session without prior Task tool invocation",
        "signals": ["enki_report"],
        "match_type": "sequence_violation",
        "requires_absence": "Task",
    },
    "trivial_test_write": {
        "weight": 4.0,
        "description": "Writing trivially-passing tests to satisfy QA gate",
        "signals": ["assert True", "pass\n", "expect(true).toBe(true)"],
        "match_type": "file_content_contains",
        "file_pattern": "test|spec",
    },
    "manual_phase_advance": {
        "weight": 4.0,
        "description": "Advancing phase without proper gate completion",
        "signals": ["action='advance'", "action=\"advance\"", "\"action\": \"advance\""],
        "match_type": "tool_input_contains",
        "tool_name": "enki_phase",
    },
    "out_of_scope_write": {
        "weight": 2.0,
        "description": "Writing to file not in task assigned_files",
        "signals": [],
        "match_type": "scope_escape",
    },
    "self_certification": {
        "weight": 4.0,
        "description": "Agent marking own work complete without validation",
        "signals": ["status='completed'", "status=\"completed\"", "\"status\": \"completed\""],
        "match_type": "tool_input_contains",
        "tool_name": "enki_report",
        "context_check": "no_prior_validator",
    },
    "repeated_gate_bypass": {
        "weight": 6.0,
        "description": "Multiple gate bypasses in same session",
        "signals": [],
        "match_type": "cumulative_count",
        "threshold_count": 2,
        "triggers_on": ["force_flag_use", "manual_phase_advance"],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_config(key: str) -> str | None:
    """Read from ~/.enki/config.json."""
    try:
        config_path = ENKI_ROOT / "config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text())
            return config.get(key)
    except Exception:
        pass
    return None


def _ensure_drift_patterns() -> None:
    """Write default drift patterns on first run if absent."""
    try:
        ENKI_ROOT.mkdir(parents=True, exist_ok=True)
        if not DRIFT_PATTERNS_PATH.exists():
            DRIFT_PATTERNS_PATH.write_text(json.dumps(DEFAULT_DRIFT_PATTERNS, indent=2))
    except Exception:
        pass


def _load_patterns() -> dict:
    _ensure_drift_patterns()
    try:
        if DRIFT_PATTERNS_PATH.exists():
            loaded = json.loads(DRIFT_PATTERNS_PATH.read_text())
            if isinstance(loaded, dict):
                return loaded
    except Exception:
        pass
    return DEFAULT_DRIFT_PATTERNS.copy()


def score_tool_call(
    session_id: str,
    tool_name: str,
    tool_input: dict,
    tool_output: dict,
    project: str | None = None,
) -> dict:
    """Score a tool call for drift contribution."""
    _ = tool_output
    patterns = _load_patterns()
    tool_input_str = json.dumps(tool_input)[:500]

    contribution = 0.0
    pattern_matched = None

    for pattern_name, pattern in patterns.items():
        match_type = pattern.get("match_type", "tool_input_contains")
        signals = pattern.get("signals", [])
        pattern_tool = pattern.get("tool_name")

        if pattern_tool and pattern_tool != tool_name:
            continue

        if match_type == "tool_input_contains":
            if any(sig in tool_input_str for sig in signals):
                contribution = max(contribution, float(pattern.get("weight", 1.0)))
                pattern_matched = pattern_name
                break

        elif match_type == "file_content_contains":
            file_path = (tool_input.get("file_path") or tool_input.get("path", "")).lower()
            file_pattern = (pattern.get("file_pattern") or "").lower()
            content = tool_input.get("content") or tool_input.get("new_string", "")
            if file_pattern and file_pattern in file_path and any(sig in content for sig in signals):
                contribution = max(contribution, float(pattern.get("weight", 1.0)))
                pattern_matched = pattern_name
                break

    cumulative = _update_drift(
        session_id=session_id,
        tool_name=tool_name,
        tool_input_summary=tool_input_str,
        contribution=contribution,
        pattern_matched=pattern_matched,
        project=project,
    )

    action = "none"
    message = None

    if cumulative >= ESCALATE_THRESHOLD:
        action = "escalate"
        message = (
            f"Enki drift escalation: session {session_id[:8]} cumulative drift score "
            f"{cumulative:.1f} (threshold {ESCALATE_THRESHOLD}). "
            f"Last pattern: {pattern_matched or 'none'}."
        )
        _record_escalation(session_id, cumulative, project)
    elif cumulative >= NUDGE_THRESHOLD:
        try:
            from enki.db import uru_db
            with uru_db() as conn:
                row = conn.execute(
                    "SELECT nudge_count FROM session_drift WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            nudge_count = int(row["nudge_count"] if row else 0)
        except Exception:
            nudge_count = 0

        if nudge_count == 0:
            action = "nudge"
            message = (
                f"Drift score {cumulative:.1f} approaching threshold. "
                f"Pattern: {pattern_matched or 'accumulation'}."
            )
            _record_nudge(session_id)

    return {
        "drift_contribution": contribution,
        "cumulative_drift": cumulative,
        "pattern_matched": pattern_matched,
        "action": action,
        "message": message,
    }


def _update_drift(
    session_id: str,
    tool_name: str,
    tool_input_summary: str,
    contribution: float,
    pattern_matched: str | None,
    project: str | None,
) -> float:
    """Update session_drift and record drift event."""
    event_id = hashlib.md5(f"{session_id}:{tool_name}:{_now()}".encode()).hexdigest()
    try:
        from enki.db import uru_db
        with uru_db() as conn:
            row = conn.execute(
                "SELECT cumulative_score FROM session_drift WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            current = float(row["cumulative_score"] if row else 0.0)
            new_cumulative = current + float(contribution)

            conn.execute(
                "INSERT INTO session_drift (session_id, cumulative_score, last_updated, project) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "cumulative_score = excluded.cumulative_score, "
                "last_updated = excluded.last_updated, "
                "project = excluded.project",
                (session_id, new_cumulative, _now(), project or ""),
            )

            if contribution > 0:
                conn.execute(
                    "INSERT INTO drift_events "
                    "(id, session_id, timestamp, tool_name, tool_input_summary, "
                    "drift_contribution, cumulative_drift, pattern_matched) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        session_id,
                        _now(),
                        tool_name,
                        tool_input_summary[:200],
                        float(contribution),
                        new_cumulative,
                        pattern_matched,
                    ),
                )
            conn.commit()
            return new_cumulative
    except Exception:
        return 0.0


def _record_escalation(session_id: str, cumulative: float, project: str | None) -> None:
    """Mark escalation and add escalation event."""
    _ = cumulative
    try:
        from enki.db import uru_db
        with uru_db() as conn:
            conn.execute(
                "UPDATE session_drift SET escalated = 1 WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "INSERT INTO drift_events (id, session_id, timestamp, event_type, details, pattern_matched, tool_name, cumulative_drift) "
                "VALUES (?, ?, ?, 'escalated', ?, ?, ?, ?)",
                (
                    hashlib.md5(f"escalated:{session_id}:{_now()}".encode()).hexdigest(),
                    session_id,
                    _now(),
                    json.dumps({"project": project or ""}),
                    "escalate_threshold",
                    "sentrux",
                    cumulative,
                ),
            )
            conn.commit()
    except Exception:
        pass


def _record_nudge(session_id: str) -> None:
    """Increment nudge counter and record nudge event."""
    try:
        from enki.db import uru_db
        with uru_db() as conn:
            conn.execute(
                "UPDATE session_drift SET nudge_count = nudge_count + 1 WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "INSERT INTO drift_events (id, session_id, timestamp, event_type, pattern_matched, tool_name) "
                "VALUES (?, ?, ?, 'nudge_fired', ?, ?)",
                (
                    hashlib.md5(f"nudge:{session_id}:{_now()}".encode()).hexdigest(),
                    session_id,
                    _now(),
                    "nudge_threshold",
                    "sentrux",
                ),
            )
            conn.commit()
    except Exception:
        pass


def send_telegram_escalation(message: str) -> None:
    """Send drift escalation notification via Telegram."""
    try:
        bot_token = os.environ.get("ENKI_TELEGRAM_BOT_TOKEN") or _read_config("telegram_bot_token")
        chat_id = os.environ.get("ENKI_TELEGRAM_CHAT_ID") or _read_config("telegram_chat_id")
        if not bot_token or not chat_id:
            return

        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
