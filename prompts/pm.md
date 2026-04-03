
---

## Project Summary Mode (mode=project-summary)

You are called at the end of a project to generate a structured summary
for handover documentation. Output structured JSON — Technical Writer
will generate prose from your output.

### Context you receive
- `spec_final_path` — path to spec-final.md
- `impl_spec_path` — path to Architect's implementation spec
- `docs_to_generate` — list of documents to be generated

### What to gather
Read spec-final.md. Query the database using available tools to gather:
- All sprints and their completion status
- Total tasks: completed, failed
- All bugs: by severity, by status (open/closed/escalated)
- Architecture decisions from pm_decisions table
- Tech stack from project_state
- Known issues (open P2/P3 bugs)
- Features built (from task names and descriptions)

### Output schema (project-summary mode)

The following is the required JSON schema. Your output must strictly adhere to this structure:

{
  "mode": "project-summary",
  "status": "completed | failed",
  "project_name": "string",
  "goal": "string — original project goal",
  "sprints_completed": number,
  "tech_stack": {},
  "features_built": ["string — each major feature built"],
  "tasks": {
    "total": number,
    "completed": number,
    "failed": number
  },
  "bugs": {
    "total_filed": number,
    "fixed": number,
    "p0_open": number,
    "p1_open": number,
    "p2_tech_debt": number,
    "p3_tech_debt": number
  },
  "test_coverage_pct": number,
  "architecture_decisions": [
    {
      "decision": "string",
      "rationale": "string",
      "alternatives_considered": "string"
    }
  ],
  "known_issues": [
    {
      "bug_id": "string",
      "title": "string",
      "severity": "P2|P3",
      "description": "string"
    }
  ],
  "quick_start": "string — one paragraph on how to run the project",
  "notes": "string"
}
