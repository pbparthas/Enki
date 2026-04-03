"""4-pass codebase scanner: discovery -> parse -> link -> enrich."""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone

from enki.db import graph_db
from enki.graph.languages import detect_language, is_source_file
from enki.graph.schema import create_graph_tables


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- Pass 1: Discovery -------------------------------------------------------

def discover_files(project_path: str) -> list[dict]:
    """Walk project directory and collect all source files."""
    files = []
    for root, dirs, filenames in os.walk(project_path):
        dirs[:] = [
            d for d in dirs
            if d not in {
                "node_modules", ".git", "__pycache__", ".venv", "venv",
                "dist", "build", ".next", ".cache", "coverage",
                ".worktrees", ".enki",
            }
        ]
        for filename in filenames:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, project_path)
            if not is_source_file(rel_path):
                continue
            try:
                stat = os.stat(full_path)
                files.append({
                    "path": rel_path,
                    "full_path": full_path,
                    "language": detect_language(rel_path),
                    "size_bytes": stat.st_size,
                    "last_modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                })
            except OSError:
                continue
    return files


# -- Pass 2: Parse -----------------------------------------------------------

def parse_file(file_info: dict) -> list[dict]:
    """Extract symbols from a source file using tree-sitter."""
    symbols = []
    language = file_info.get("language")
    if not language:
        return symbols

    try:
        from tree_sitter_languages import get_parser

        parser = get_parser(language)
        with open(file_info["full_path"], "rb") as f:
            source = f.read()

        tree = parser.parse(source)
        symbols = _extract_symbols(
            tree.root_node, source, file_info["path"], language
        )
    except Exception:
        pass

    return symbols


def _extract_symbols(node, source: bytes, file_path: str, language: str) -> list[dict]:
    """Walk AST and extract named symbols."""
    symbols = []

    extractable = {
        "typescript": {
            "function_declaration", "method_definition", "arrow_function",
            "class_declaration", "interface_declaration", "type_alias_declaration",
            "export_statement", "variable_declarator",
        },
        "python": {
            "function_definition", "async_function_definition",
            "class_definition", "decorated_definition",
        },
        "javascript": {
            "function_declaration", "method_definition", "arrow_function",
            "class_declaration", "variable_declarator",
        },
        "go": {
            "function_declaration", "method_declaration",
            "type_declaration", "interface_type",
        },
        "rust": {
            "function_item", "impl_item", "struct_item",
            "trait_item", "enum_item", "type_item",
        },
        "java": {
            "method_declaration", "class_declaration",
            "interface_declaration", "constructor_declaration",
        },
    }

    target_types = extractable.get(language, set())

    def walk(n):
        if n.type in target_types:
            name = _extract_name(n, source)
            if name:
                symbol_id = f"{file_path}::{name}::{n.start_point[0]}"
                symbols.append({
                    "id": symbol_id,
                    "file_path": file_path,
                    "name": name,
                    "kind": _classify_kind(n.type, language),
                    "line_start": n.start_point[0],
                    "line_end": n.end_point[0],
                    "signature": _extract_signature(n, source),
                    "complexity": _compute_complexity(n),
                    "is_exported": _is_exported(n, source, language),
                })
        for child in n.children:
            walk(child)

    walk(node)
    return symbols


def _extract_name(node, source: bytes) -> str | None:
    """Extract the name identifier from a symbol node."""
    for child in node.children:
        if child.type in {"identifier", "property_identifier", "type_identifier", "field_identifier"}:
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return None


def _classify_kind(node_type: str, language: str) -> str:
    _ = language
    kind_map = {
        "function_declaration": "function",
        "function_definition": "function",
        "async_function_definition": "function",
        "function_item": "function",
        "method_declaration": "method",
        "method_definition": "method",
        "arrow_function": "function",
        "class_declaration": "class",
        "class_definition": "class",
        "interface_declaration": "interface",
        "interface_type": "interface",
        "type_alias_declaration": "type",
        "type_declaration": "type",
        "struct_item": "struct",
        "trait_item": "trait",
        "enum_item": "enum",
        "export_statement": "export",
        "variable_declarator": "const",
        "decorated_definition": "function",
        "impl_item": "impl",
        "constructor_declaration": "constructor",
    }
    return kind_map.get(node_type, "symbol")


def _extract_signature(node, source: bytes) -> str:
    """Extract first line of symbol as signature."""
    start = node.start_byte
    end = min(node.start_byte + 200, node.end_byte)
    sig = source[start:end].decode("utf-8", errors="replace")
    return sig.split("\n")[0].strip()[:200]


def _compute_complexity(node) -> int:
    """Approximate cyclomatic complexity by counting decision nodes."""
    decision_types = {
        "if_statement", "elif_clause", "else_clause",
        "for_statement", "while_statement", "do_statement",
        "switch_statement", "case_clause",
        "ternary_expression", "conditional_expression",
        "catch_clause", "try_statement",
        "&&", "||", "and", "or",
    }
    count = 1

    def walk(n):
        nonlocal count
        if n.type in decision_types:
            count += 1
        for child in n.children:
            walk(child)

    walk(node)
    return count


def _is_exported(node, source: bytes, language: str) -> int:
    """Check if symbol is exported/public."""
    if language in ("typescript", "javascript"):
        sig = source[node.start_byte:min(node.start_byte + 20, node.end_byte)]
        return 1 if b"export" in sig else 0
    if language == "python":
        name = _extract_name(node, source) or ""
        return 0 if name.startswith("_") else 1
    if language in ("java", "kotlin"):
        sig = source[node.start_byte:min(node.start_byte + 30, node.end_byte)]
        return 1 if b"public" in sig else 0
    return 0


# -- Pass 3: Link ------------------------------------------------------------

def extract_imports(file_info: dict) -> list[dict]:
    """Extract import/require statements from a file."""
    imports = []
    language = file_info.get("language")
    if not language:
        return imports

    try:
        from tree_sitter_languages import get_parser

        parser = get_parser(language)
        with open(file_info["full_path"], "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        imports = _extract_import_edges(
            tree.root_node, source, file_info["path"], language
        )
    except Exception:
        pass

    return imports


def _extract_import_edges(node, source: bytes, file_path: str, language: str) -> list[dict]:
    """Extract import edges from AST."""
    edges = []
    import_types = {
        "typescript": {"import_statement", "import_declaration"},
        "python": {"import_statement", "import_from_statement"},
        "javascript": {"import_statement", "import_declaration"},
        "go": {"import_declaration", "import_spec"},
        "rust": {"use_declaration"},
        "java": {"import_declaration"},
    }
    target_types = import_types.get(language, set())

    def walk(n):
        if n.type in target_types:
            for child in n.children:
                if child.type in {"string", "dotted_name", "scoped_identifier"}:
                    raw = source[child.start_byte:child.end_byte].decode(
                        "utf-8", errors="replace"
                    ).strip("\"'")
                    edge_id = hashlib.md5(
                        f"{file_path}::imports::{raw}".encode()
                    ).hexdigest()
                    edges.append({
                        "id": edge_id,
                        "from_id": file_path,
                        "to_id": raw,
                        "edge_type": "imports",
                        "line_number": n.start_point[0],
                    })
        for child in n.children:
            walk(child)

    walk(node)
    return edges


def resolve_import_path(import_raw: str, from_file: str, all_files: set[str], project_path: str) -> str | None:
    """Resolve a raw import string to a project-relative file path."""
    _ = project_path
    if not import_raw.startswith("."):
        return None

    from_dir = os.path.dirname(from_file)
    candidate = os.path.normpath(os.path.join(from_dir, import_raw))

    for ext in [".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"]:
        candidate_with_ext = candidate + ext
        if candidate_with_ext in all_files:
            return candidate_with_ext

    for index in ["index.ts", "index.js", "__init__.py", "mod.rs"]:
        candidate_index = os.path.join(candidate, index)
        if candidate_index in all_files:
            return candidate_index

    return None


# -- Pass 4: Enrich ----------------------------------------------------------

def compute_blast_radius(project: str, conn: sqlite3.Connection) -> None:
    """Compute blast radius for all exported symbols."""
    _ = project
    edges = conn.execute(
        "SELECT from_id, to_id, edge_type FROM edges WHERE edge_type='imports'"
    ).fetchall()

    importers: dict[str, set[str]] = {}
    for edge in edges:
        to_file = edge["to_id"]
        from_file = edge["from_id"]
        if to_file not in importers:
            importers[to_file] = set()
        importers[to_file].add(from_file)

    total_files = conn.execute("SELECT COUNT(*) as c FROM files").fetchone()["c"]
    if total_files == 0:
        return

    symbols = conn.execute(
        "SELECT id, file_path FROM symbols WHERE is_exported=1"
    ).fetchall()

    for symbol in symbols:
        file_path = symbol["file_path"]
        direct = len(importers.get(file_path, set()))

        visited: set[str] = set()
        queue = list(importers.get(file_path, set()))
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(importers.get(current, set()) - visited)
        transitive = len(visited)

        blast_score = min(1.0, transitive / max(total_files * 0.1, 1))
        risk_level = (
            "critical" if blast_score >= 0.8 else
            "high" if blast_score >= 0.5 else
            "medium" if blast_score >= 0.2 else
            "low"
        )

        conn.execute(
            "INSERT OR REPLACE INTO blast_radius "
            "(symbol_id, file_path, direct_importers, transitive_importers, "
            "blast_score, risk_level, last_computed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                symbol["id"], file_path, direct, transitive,
                blast_score, risk_level, _now(),
            ),
        )

    conn.commit()


# -- Full scan orchestrator --------------------------------------------------

def run_full_scan(project: str, project_path: str) -> dict:
    """Run all 4 passes and populate graph.db for a project."""
    stats = {
        "files_scanned": 0,
        "symbols_extracted": 0,
        "edges_found": 0,
        "blast_radius_computed": 0,
        "errors": [],
    }

    with graph_db(project) as conn:
        create_graph_tables(conn)
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM blast_radius")
        conn.commit()

        files = discover_files(project_path)
        stats["files_scanned"] = len(files)
        all_file_paths = {f["path"] for f in files}

        for file_info in files:
            conn.execute(
                "INSERT OR REPLACE INTO files "
                "(path, language, size_bytes, last_modified, last_scanned) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    file_info["path"], file_info["language"],
                    file_info["size_bytes"], file_info["last_modified"], _now(),
                ),
            )
        conn.commit()

        for file_info in files:
            try:
                symbols = parse_file(file_info)
                stats["symbols_extracted"] += len(symbols)
                for sym in symbols:
                    conn.execute(
                        "INSERT OR REPLACE INTO symbols "
                        "(id, file_path, name, kind, line_start, line_end, "
                        "signature, complexity, is_exported) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sym["id"], sym["file_path"], sym["name"], sym["kind"],
                            sym["line_start"], sym["line_end"], sym["signature"],
                            sym["complexity"], sym["is_exported"],
                        ),
                    )
                conn.execute(
                    "UPDATE files SET symbol_count=? WHERE path=?",
                    (len(symbols), file_info["path"]),
                )
            except Exception as e:
                stats["errors"].append(f"{file_info['path']}: {e}")
        conn.commit()

        for file_info in files:
            try:
                raw_imports = extract_imports(file_info)
                for imp in raw_imports:
                    resolved = resolve_import_path(
                        imp["to_id"], imp["from_id"], all_file_paths, project_path
                    )
                    if resolved:
                        imp["to_id"] = resolved
                        conn.execute(
                            "INSERT OR REPLACE INTO edges "
                            "(id, from_id, to_id, edge_type, line_number) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                imp["id"], imp["from_id"], imp["to_id"],
                                imp["edge_type"], imp["line_number"],
                            ),
                        )
                        stats["edges_found"] += 1
            except Exception as e:
                stats["errors"].append(f"link {file_info['path']}: {e}")
        conn.commit()

        try:
            compute_blast_radius(project, conn)
            stats["blast_radius_computed"] = conn.execute(
                "SELECT COUNT(*) as c FROM blast_radius"
            ).fetchone()["c"]
        except Exception as e:
            stats["errors"].append(f"blast radius: {e}")

        conn.execute(
            "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
            ("last_full_scan", _now()),
        )
        conn.commit()

    return stats


# -- Incremental update ------------------------------------------------------

def run_incremental_update(project: str, project_path: str) -> dict:
    """Update graph for files changed since last scan."""
    stats = {"files_updated": 0, "errors": []}

    with graph_db(project) as conn:
        create_graph_tables(conn)
        last_scan_row = conn.execute(
            "SELECT value FROM scan_state WHERE key='last_full_scan'"
        ).fetchone()
        if not last_scan_row:
            return run_full_scan(project, project_path)
        last_scan = last_scan_row["value"]

    try:
        import subprocess

        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"--since={last_scan}"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        changed_files = [
            f.strip()
            for f in result.stdout.splitlines()
            if f.strip() and is_source_file(f.strip())
        ]
    except Exception:
        return stats

    if not changed_files:
        return stats

    with graph_db(project) as conn:
        all_files = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}

        for rel_path in changed_files:
            full_path = os.path.join(project_path, rel_path)
            if not os.path.exists(full_path):
                conn.execute("DELETE FROM files WHERE path=?", (rel_path,))
                conn.execute("DELETE FROM symbols WHERE file_path=?", (rel_path,))
                conn.execute("DELETE FROM edges WHERE from_id=? OR to_id=?", (rel_path, rel_path))
                continue

            lang = detect_language(rel_path)
            if not lang:
                continue

            file_info = {
                "path": rel_path,
                "full_path": full_path,
                "language": lang,
                "size_bytes": os.path.getsize(full_path),
            }

            conn.execute("DELETE FROM symbols WHERE file_path=?", (rel_path,))
            conn.execute("DELETE FROM edges WHERE from_id=?", (rel_path,))

            symbols = parse_file(file_info)
            for sym in symbols:
                conn.execute(
                    "INSERT OR REPLACE INTO symbols "
                    "(id, file_path, name, kind, line_start, line_end, "
                    "signature, complexity, is_exported) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sym["id"], sym["file_path"], sym["name"], sym["kind"],
                        sym["line_start"], sym["line_end"], sym["signature"],
                        sym["complexity"], sym["is_exported"],
                    ),
                )

            raw_imports = extract_imports(file_info)
            for imp in raw_imports:
                resolved = resolve_import_path(
                    imp["to_id"], imp["from_id"], all_files, project_path
                )
                if resolved:
                    imp["to_id"] = resolved
                    conn.execute(
                        "INSERT OR REPLACE INTO edges "
                        "(id, from_id, to_id, edge_type, line_number) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            imp["id"], imp["from_id"], imp["to_id"],
                            imp["edge_type"], imp["line_number"],
                        ),
                    )

            conn.execute(
                "UPDATE files SET symbol_count=?, last_scanned=? WHERE path=?",
                (len(symbols), _now(), rel_path),
            )
            stats["files_updated"] += 1

        conn.commit()
        compute_blast_radius(project, conn)
        conn.execute(
            "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
            ("last_full_scan", _now()),
        )
        conn.commit()

    return stats

