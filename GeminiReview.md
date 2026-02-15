This code review covers the Enki ecosystem, including the Python backend, MCP server, shell hooks, and configuration specifications.

---

### CRITICAL SEVERITY

**File: `./scripts/hooks/enki-pre-tool-use.sh` (and others)**
- **Severity:** CRITICAL
- **Category:** SHELL / PORTABILITY
- **Problem:** Hardcoded absolute paths to a specific user's directory (`/home/partha/Desktop/Enki/.venv/bin/enki`).
- **Fix:** Use `PATH` lookup or a relative path from the project root. Use `ENKI_BIN="${ENKI_DIR}/.venv/bin/enki"` where `ENKI_DIR` is dynamically resolved.

**File: `./scripts/hooks/enki-pre-tool-use.sh` (Line 23)**
- **Severity:** CRITICAL
- **Category:** SHELL / SECURITY
- **Problem:** Unquoted variable usage: `echo "$(date): $INPUT" >> /tmp/enki-hook-debug.log`. If `$INPUT` (the tool JSON) contains shell-sensitive characters, this could lead to unintended behavior or log injection.
- **Fix:** Always quote variables: `echo "$(date): $INPUT"`.

**File: `./src/enki/api_server.py` (Line 245)**
- **Severity:** CRITICAL
- **Category:** ERROR_HANDLING / SECURITY
- **Problem:** Catch-all exception handler in the `api_remember` and `api_recall` endpoints returns the raw exception string to the client. This leaks server-side implementation details (tracebacks, file paths).
- **Fix:** Log the error server-side and return a generic "Internal Server Error" message with a correlation ID.

---

### HIGH SEVERITY

**File: `./src/enki/transcript.py` (Line 72)**
- **Severity:** HIGH
- **Category:** ERROR_HANDLING
- **Problem:** `parse_transcript` contains a bare `except:` when catching `json.loads` failures. This can swallow `KeyboardInterrupt` or `SystemExit`.
- **Fix:** Change to `except json.JSONDecodeError:`.

**File: `./src/enki/api_server.py` (Line 38)**
- **Severity:** HIGH
- **Category:** ARCHITECTURE
- **Problem:** Uses `@app.on_event("startup")`, which is deprecated in FastAPI. 
- **Fix:** Use the `lifespan` context manager pattern for database initialization.

**File: `./src/enki/worktree.py` (Line 131)**
- **Severity:** HIGH
- **Category:** SHELL / ERROR_HANDLING
- **Problem:** `merge_worktree` calls `git checkout target_branch` on the main project directory. If the user has uncommitted changes in the main repo, this will fail or cause data loss, and the script doesn't check for "dirty" state before acting.
- **Fix:** Add a `git diff --quiet` check before attempting to switch branches for merging.

**File: `./src/enki/ereshkigal.py` (Line 183)**
- **Severity:** HIGH
- **Category:** ERROR_HANDLING
- **Problem:** Bare `except Exception:` inside the regex matching loop. If a regex is invalid, it silently continues, potentially allowing a tool use that should have been blocked.
- **Fix:** Specifically catch `re.error` and log it as a configuration error.

---

### MEDIUM SEVERITY

**File: `./src/enki/db.py` (Line 13)**
- **Severity:** MEDIUM
- **Category:** SOLID
- **Problem:** Global state `_local = threading.local()` is used to manage connections. This makes testing difficult and tightly couples the database logic to a specific concurrency model.
- **Fix:** Use a dependency injection pattern or a proper Connection Pooler.

**File: `./src/enki/retention.py` (Lines 37-52)**
- **Severity:** MEDIUM
- **Category:** ARCHITECTURE
- **Problem:** Complexity is high. It handles strings, ints, floats, and objects for `created_at`. This suggests a lack of data normalization at the boundary.
- **Fix:** Ensure all timestamps are converted to aware `datetime` objects in the `Bead.from_row` method rather than at the point of use.

**File: `./src/enki/orchestrator.py` (Lines 17-100)**
- **Severity:** MEDIUM
- **Category:** SOLID (Single Responsibility)
- **Problem:** The `AGENTS` dictionary is a massive hardcoded configuration block inside a logic module.
- **Fix:** Move agent definitions to a separate `agents.json` or `config.py` file.

**File: `./src/enki/client.py` (Line 160)**
- **Severity:** MEDIUM
- **Category:** ARCHITECTURE
- **Problem:** Local embedding computation happens synchronously inside the API call methods. This will freeze the CLI/MCP server for 100-500ms per call.
- **Fix:** Move embedding generation to an async worker or a dedicated thread pool.

---

### LOW SEVERITY

**File: `./src/enki/persona.py` (Line 168)**
- **Severity:** LOW
- **Category:** CONFIG
- **Problem:** `DEFAULT_USER` is hardcoded to "Partha".
- **Fix:** Load user identity from a global configuration file in `~/.enki/config.yaml`.

**File: `./src/enki/migration.py` (Line 245)**
- **Severity:** LOW
- **Category:** ARCHITECTURE
- **Problem:** `_generate_all_embeddings` uses a custom `struct.pack` format instead of using the helper in `embeddings.py`.
- **Fix:** Use `vector_to_blob` from `embeddings.py` to maintain DRY principle.

---

### Summary Table

| Category | CRITICAL | HIGH | MEDIUM | LOW |
| :--- | :---: | :---: | :---: | :---: |
| SOLID | 0 | 0 | 2 | 0 |
| ERROR_HANDLING | 1 | 2 | 1 | 0 |
| SHELL | 2 | 1 | 0 | 0 |
| CONFIG | 0 | 0 | 0 | 1 |
| ARCHITECTURE | 0 | 1 | 2 | 1 |
| **TOTAL** | **3** | **4** | **5** | **2** |

---

### Top 5 Architectural Concerns

1.  **Hardcoded Environment Assumptions:** The shell hooks are tightly coupled to a specific machine's filesystem structure. This prevents Enki from being used in a team environment or across multiple machines without manual script editing.
2.  **Concurrency Conflicts:** The system uses SQLite in `WAL` mode with a thread-local connection. However, the MCP server, the CLI, and the shell hooks (launched by Claude Code) are **separate processes**. There is a risk of "Database is locked" errors if a hook triggers a write while the MCP server is indexing.
3.  **Process-Logic Coupling:** `ereshkigal.py` (the challenger) and `enforcement.py` (the gates) share logic but have no formal interface. If the gate logic changes, the interceptor might not know, creating "blind spots."
4.  **Heavyweight Initialization:** The `embeddings` module lazy-loads `sentence-transformers`. While this saves time on simple commands, the first call to `remember` or `recall` will suffer a multi-second delay while the model loads into RAM.
5.  **Heuristic Reliability:** `transcript.py` relies on regex to find "decisions." As LLM models change their speaking style (e.g., from "I will" to "I'm going to"), the memory system's ability to extract knowledge will silently degrade.

### Module Test Coverage Analysis

*   **Weakest Coverage (Logic Heavy, High Risk):**
    *   `migration.py`: Complex file I/O and SQL mapping with zero provided tests.
    *   `api_server.py`: Auth logic and search routing are untested in the provided dump.
    *   `transcript.py`: Very high regex complexity; requires extensive edge-case testing for malformed JSONL.
*   **Strongest Coverage (Heuristic):**
    *   `beads.py` and `db.py` appear to follow standard CRUD patterns that are easily testable.
    *   `enforcement.py` and `ereshkigal.py` have associated test files, though they must be checked for "bypass" attempts (testing if things that *should* block actually do).