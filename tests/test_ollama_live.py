"""Live Ollama tests for all 5 local_model operations.

Run manually: .venv/bin/python3 tests/test_ollama_live.py
Requires Ollama running with llama3.2:3b loaded.
"""

import json
import sys
sys.path.insert(0, "src")

from enki.local_model import (
    construct_note,
    classify_links,
    check_evolution,
    extract_code_knowledge,
    extract_from_transcript,
    is_available,
)


def test_construct_note():
    print("=== Test 1: construct_note ===")
    result = construct_note(
        "Decided to use JWT tokens instead of sessions because we need "
        "stateless scaling across multiple servers",
        "decision",
    )
    print(json.dumps(result, indent=2))
    assert isinstance(result.get("keywords"), list), f"keywords should be list, got {type(result.get('keywords'))}"
    assert isinstance(result.get("tags"), list), f"tags should be list, got {type(result.get('tags'))}"
    assert isinstance(result.get("summary"), str), "summary should be str"
    assert isinstance(result.get("context_description"), str), "context_description should be str"
    print("PASS\n")


def test_classify_links():
    print("=== Test 2: classify_links ===")
    result = classify_links(
        "Use Redis for session caching to reduce database load",
        "decision",
        [
            {
                "note_id": "n1",
                "content": "Decided to use PostgreSQL as primary database",
                "category": "decision",
                "source_db": "wisdom",
                "score": 0.8,
            },
            {
                "note_id": "n2",
                "content": "JWT tokens chosen for stateless auth",
                "category": "decision",
                "source_db": "wisdom",
                "score": 0.7,
            },
        ],
    )
    print(json.dumps(result, indent=2))
    assert isinstance(result, list), f"should be list, got {type(result)}"
    for link in result:
        assert "target_id" in link, "missing target_id"
        assert "relationship" in link, "missing relationship"
    print("PASS\n")


def test_check_evolution():
    print("=== Test 3: check_evolution ===")
    result = check_evolution(
        "Redis cluster mode provides better fault tolerance than standalone",
        "learning",
        "redis,cluster,fault-tolerance",
        "Use Redis for caching to improve performance",
        "decision",
        "redis,caching,performance",
    )
    print(json.dumps(result, indent=2) if result else "None (no evolution)")
    assert result is None or isinstance(result, dict), f"should be None or dict, got {type(result)}"
    if result:
        # Check proposed fields are present
        assert any(k in result for k in ("proposed_context", "proposed_keywords", "proposed_tags"))
    print("PASS\n")


def test_extract_code_knowledge():
    print("=== Test 4: extract_code_knowledge ===")
    code = (
        "from contextlib import contextmanager\n"
        "from pathlib import Path\n\n"
        "# WAL mode is critical for concurrent read/write support\n"
        "WAL_PRAGMA = 'PRAGMA journal_mode=WAL'\n\n"
        "@contextmanager\n"
        "def connect(db_path):\n"
        "    conn = open_connection(str(db_path))\n"
        "    conn.execute(WAL_PRAGMA)\n"
        "    conn.execute('PRAGMA busy_timeout=5000')\n"
        "    conn.execute('PRAGMA foreign_keys=ON')\n"
        "    try:\n"
        "        yield conn\n"
        "        conn.commit()\n"
        "    except Exception:\n"
        "        conn.rollback()\n"
        "        raise\n"
        "    finally:\n"
        "        conn.close()\n"
    )
    result = extract_code_knowledge(code, "src/db.py")
    print(json.dumps(result, indent=2))
    assert isinstance(result, list), f"should be list, got {type(result)}"
    for item in result:
        assert isinstance(item.get("keywords"), list), f"keywords should be list, got {type(item.get('keywords'))}"
        assert item.get("category") == "code_knowledge"
        assert item.get("file_ref") == "src/db.py"
    print("PASS\n")


def test_extract_from_transcript():
    print("=== Test 5: extract_from_transcript ===")
    transcript = (
        "User: We should use FastAPI instead of Flask for this project.\n"
        "Assistant: Agreed. FastAPI gives us async support and automatic OpenAPI docs. "
        "I'll set up the project structure with FastAPI.\n"
        "User: Also make sure we use Pydantic v2 for validation.\n"
        "Assistant: Done. I've configured Pydantic v2 with strict mode enabled. "
        "This caught a bug where we were passing strings as integers.\n"
    )
    result = extract_from_transcript(transcript, project="myapp")
    print(json.dumps(result, indent=2))
    assert isinstance(result, list), f"should be list, got {type(result)}"
    for item in result:
        assert isinstance(item.get("keywords"), list) or isinstance(item.get("keywords"), str), \
            f"keywords type: {type(item.get('keywords'))}"
        assert item.get("category") in ("decision", "learning", "pattern", "fix")
    print("PASS\n")


if __name__ == "__main__":
    if not is_available():
        print("ERROR: Ollama not available with llama3.2:3b")
        sys.exit(1)

    print("Ollama available. Running live tests...\n")
    passed = 0
    failed = 0
    for test in [
        test_construct_note,
        test_classify_links,
        test_check_evolution,
        test_extract_code_knowledge,
        test_extract_from_transcript,
    ]:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {e}\n")
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
