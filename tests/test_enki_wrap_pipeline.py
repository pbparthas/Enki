"""Unit tests for enki_wrap transcript parsing/chunking/staging helpers."""

import json
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod


def _patch_env(root: Path):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=root,
        DB_DIR=root / "db",
    )


def _make_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_extract_wrap_messages_from_jsonl(tmp_path):
    transcript = tmp_path / "sess-1.jsonl"
    _make_jsonl(
        transcript,
        [
            {"type": "system", "message": {"content": "ignore me"}},
            {"type": "user", "message": {"content": "hello"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "name": "Read"}]}},
            {"type": "progress", "message": {"content": "skip"}},
        ],
    )
    from enki.mcp.orch_tools import _extract_wrap_messages

    messages = _extract_wrap_messages(transcript)
    assert messages == ["USER: hello", "ASSISTANT: hi"]


def test_truncate_long_code_blocks():
    from enki.mcp.orch_tools import _truncate_long_code_blocks

    code = "\n".join([f"line {i}" for i in range(1, 30)])
    text = f"before\n```\n{code}\n```\nafter"
    out = _truncate_long_code_blocks(text, max_lines=20)
    assert "... [code truncated]" in out
    assert "line 1" in out and "line 3" in out
    assert "line 25" not in out


def test_chunk_wrap_messages_boundary():
    from enki.mcp.orch_tools import _chunk_wrap_messages

    messages = [
        "USER: " + ("a" * 8000),
        "ASSISTANT: " + ("b" * 8000),
        "USER: " + ("c" * 1000),
    ]
    chunks = _chunk_wrap_messages(messages, max_chars=15000)
    assert len(chunks) == 2
    assert "a" * 100 in chunks[0]
    assert "b" * 100 in chunks[1]


def test_stage_wrap_candidates_and_deduplicate(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()

    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import _stage_wrap_candidates

        init_all()
        items = [
            {"content": "Use retries for flaky DB writes", "category": "pattern", "summary": "Retry writes", "keywords": "retry,db"},
            {"content": "Use retries for flaky DB writes", "category": "pattern", "summary": "Retry writes", "keywords": "retry,db"},
            {"content": "Fix race by serializing init", "category": "fix", "summary": "Serialize init", "keywords": "race,init"},
        ]
        staged, duplicates = _stage_wrap_candidates(items, project="Enki", session_id="sess-1")
        assert staged == 2
        assert duplicates == 1

        from enki.db import abzu_db
        with abzu_db() as conn:
            rows = conn.execute(
                "SELECT source, status FROM note_candidates ORDER BY created_at ASC"
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["source"] == "transcript-extraction"
        assert rows[0]["status"] == "raw"

    db_mod._em_initialized = old_init
