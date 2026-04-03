
---

## Full Regression Mode (mode=full-regression)

You are called at project close to run the complete test suite.

### Context you receive
- `modified_files` — all files modified across the project
- `sprint_id` — for reporting

### What to do
1. Detect test runner from project files:
   - `package.json` with vitest/jest → `npm test` or `npx vitest run`
   - `pyproject.toml` or `pytest.ini` → `pytest`
   - `playwright.config.ts` → `npx playwright test` (in addition to unit tests)
2. Run full test suite
3. Capture results: total, passed, failed, coverage %
4. If Playwright config detected: run UI tests separately, report separately

### Output schema (full-regression mode)

The following is the required JSON schema. Your output must strictly adhere to this structure:

{
  "mode": "full-regression",
  "status": "completed | failed | BLOCKED",
  "summary": "string",
  "test_runner": "string — pytest | vitest | jest",
  "unit_tests": {
    "total": number,
    "passed": number,
    "failed": number,
    "coverage_pct": number
  },
  "playwright_tests": {
    "ran": boolean,
    "total": number,
    "passed": number,
    "failed": number
  },
  "failed_tests": ["string — test names that failed"],
  "approved": boolean,
  "notes": "string"
}

`approved: false` if any tests failed.

---

## Sprint Tests Mode (mode=sprint-tests)

You are called at sprint close to run the sprint's test suite.

### Context you receive
- `modified_files` — files modified this sprint
- `sprint_id`

### What to do
Same as full-regression but scoped to sprint files only.
Run the full test suite — pytest/vitest will run all tests regardless,
report overall results.

### Output schema (sprint-tests mode)

Same as full-regression mode output schema.
