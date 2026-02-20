"""code_knowledge.py — Codebase knowledge extraction and staleness tracking.

Manages `code_knowledge` category notes with file tracking:
- SHA-256 hash of file content for change detection
- Staleness detection: compare stored hash vs current file
- Session-end code scan: git diff against primary branch → extract knowledge

Key rules:
- Staleness stays on code note only — linked notes NOT flagged
- Stale code notes are marked, not deleted (historical value)
- Code knowledge extraction only from files merged to primary branch
"""

import hashlib
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> Optional[str]:
    """Compute SHA-256 hash of a file's content.

    Returns None if file doesn't exist or can't be read.
    """
    try:
        content = Path(file_path).read_bytes()
        return hashlib.sha256(content).hexdigest()
    except (OSError, IOError):
        return None


def check_staleness(project: str = None) -> list[dict]:
    """Check all code_knowledge notes for staleness.

    Compares stored file_hash against current file content.

    Args:
        project: Optional project filter.

    Returns:
        List of stale notes: [{note_id, file_ref, stored_hash, current_hash, status}]
        status: 'stale' (hash changed), 'missing' (file gone), 'current' (unchanged)
    """
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        query = (
            "SELECT id, file_ref, file_hash FROM notes "
            "WHERE category = 'code_knowledge' AND file_ref IS NOT NULL"
        )
        params = []
        if project:
            query += " AND project = ?"
            params.append(project)

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        note_id = row["id"]
        file_ref = row["file_ref"]
        stored_hash = row["file_hash"]

        current_hash = compute_file_hash(file_ref)

        if current_hash is None:
            status = "missing"
        elif current_hash != stored_hash:
            status = "stale"
        else:
            status = "current"

        results.append({
            "note_id": note_id,
            "file_ref": file_ref,
            "stored_hash": stored_hash,
            "current_hash": current_hash,
            "status": status,
        })

    return results


def mark_stale(note_ids: list[str]) -> int:
    """Mark code_knowledge notes as stale by clearing last_verified.

    Does NOT delete notes — stale notes have historical value.

    Returns count of notes marked.
    """
    if not note_ids:
        return 0

    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        placeholders = ",".join("?" for _ in note_ids)
        cursor = conn.execute(
            f"UPDATE notes SET last_verified = NULL "
            f"WHERE id IN ({placeholders}) AND category = 'code_knowledge'",
            note_ids,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def verify_note(note_id: str) -> bool:
    """Mark a code_knowledge note as verified (current file hash matches).

    Updates last_verified timestamp and refreshes file_hash.

    Returns True if note exists and was updated.
    """
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        row = conn.execute(
            "SELECT file_ref FROM notes WHERE id = ? AND category = 'code_knowledge'",
            (note_id,),
        ).fetchone()
        if not row or not row["file_ref"]:
            return False

        current_hash = compute_file_hash(row["file_ref"])
        if current_hash is None:
            return False

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE notes SET file_hash = ?, last_verified = ? WHERE id = ?",
            (current_hash, now, note_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_changed_files(
    project_path: str, primary_branch: str = "main"
) -> list[str]:
    """Get files changed relative to primary branch via git diff.

    Only returns files that exist (merged changes).

    Args:
        project_path: Path to the git repository.
        primary_branch: Branch to diff against (default: main).

    Returns:
        List of file paths relative to project_path.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{primary_branch}...HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Try without the range (might be on primary branch)
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~5", "HEAD"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return []

        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        # Filter to existing files
        return [
            f for f in files
            if (Path(project_path) / f).exists()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


# File extensions worth scanning for code knowledge
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".yaml", ".yml", ".toml",
    ".json", ".sql", ".md",
}


def scan_changed_files(
    project_path: str,
    project_name: str,
    primary_branch: str = "main",
) -> list[dict]:
    """Scan files changed since primary branch and extract code knowledge.

    This runs at session-end. Uses the local model if available,
    falls back to heuristic extraction.

    Args:
        project_path: Path to the git repository.
        project_name: Project name for note storage.
        primary_branch: Branch to diff against.

    Returns:
        List of extracted code knowledge items ready for storage.
    """
    changed = get_changed_files(project_path, primary_branch)
    if not changed:
        return []

    items = []
    for file_path in changed:
        ext = Path(file_path).suffix
        if ext not in CODE_EXTENSIONS:
            continue

        full_path = str(Path(project_path) / file_path)
        try:
            content = Path(full_path).read_text(errors="replace")
        except (OSError, IOError):
            continue

        if len(content) < 50:  # Skip trivially small files
            continue

        file_hash = compute_file_hash(full_path)
        extracted = _extract_from_file(content, file_path)

        for item in extracted:
            item["file_ref"] = full_path
            item["file_hash"] = file_hash
            item["project"] = project_name
            item["category"] = "code_knowledge"
            items.append(item)

    return items


def _extract_from_file(content: str, file_path: str) -> list[dict]:
    """Extract code knowledge from a file.

    Tries local model first, falls back to heuristic extraction.
    """
    # Try local model
    try:
        from enki.local_model import extract_code_knowledge, is_available
        if is_available():
            return extract_code_knowledge(content, file_path)
    except (ImportError, Exception):
        pass

    # Heuristic fallback
    return _heuristic_extract(content, file_path)


def _heuristic_extract(content: str, file_path: str) -> list[dict]:
    """Simple heuristic code knowledge extraction.

    Looks for structured patterns that indicate notable code knowledge:
    - Module/class docstrings
    - Configuration constants
    - Architecture-significant patterns
    """
    items = []
    ext = Path(file_path).suffix

    # Python-specific extraction
    if ext == ".py":
        items.extend(_extract_python_knowledge(content, file_path))

    return items


def _extract_python_knowledge(content: str, file_path: str) -> list[dict]:
    """Extract knowledge from Python files."""
    items = []
    lines = content.split("\n")

    # Extract module docstring if substantial
    if lines and (lines[0].startswith('"""') or lines[0].startswith("'''")):
        quote = lines[0][:3]
        doc_lines = [lines[0][3:]]
        for i, line in enumerate(lines[1:], 1):
            if quote in line:
                doc_lines.append(line.split(quote)[0])
                break
            doc_lines.append(line)
        docstring = "\n".join(doc_lines).strip()
        if len(docstring) > 30:
            items.append({
                "content": f"Module {file_path}: {docstring}",
                "keywords": _extract_keywords_from_path(file_path),
                "summary": f"Module documentation for {file_path}",
            })

    return items


def _extract_keywords_from_path(file_path: str) -> str:
    """Extract keywords from a file path."""
    parts = Path(file_path).stem.split("_")
    return ",".join(p for p in parts if len(p) > 2)


def store_code_knowledge(items: list[dict]) -> list[str]:
    """Store extracted code knowledge items as notes in wisdom.db.

    Preferences bypass staging — code_knowledge goes direct to wisdom.db
    (like preferences, since it's machine-generated with verifiable source).

    Returns list of created note IDs.
    """
    if not items:
        return []

    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    created_ids = []
    try:
        for item in items:
            content = item.get("content", "")
            if not content:
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()

            # Skip duplicates
            existing = conn.execute(
                "SELECT id FROM notes WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if existing:
                continue

            note_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO notes "
                "(id, content, summary, keywords, category, project, "
                "file_ref, file_hash, last_verified, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id,
                    content,
                    item.get("summary"),
                    item.get("keywords"),
                    "code_knowledge",
                    item.get("project"),
                    item.get("file_ref"),
                    item.get("file_hash"),
                    now,
                    content_hash,
                ),
            )
            created_ids.append(note_id)

        conn.commit()
    finally:
        conn.close()

    return created_ids
