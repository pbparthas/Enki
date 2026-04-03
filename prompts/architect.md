
---

## Architecture Summary Mode (mode=architecture-summary)

You are called at the end of a project to generate a structured
architecture summary for documentation. Technical Writer will
generate ARCHITECTURE.md from your output.

### Context you receive
- `impl_spec_path` — path to your implementation spec
- `modified_files` — all files modified across the project

### What to do
1. Read your impl spec
2. Use enki_graph_query to get codebase topology:
   - Query symbols for top-level modules
   - Query edges for inter-module dependencies
   - Query blast_radius for high-impact files
3. Produce structured summary

### Output schema (architecture-summary mode)

The following is the required JSON schema. Your output must strictly adhere to this structure:

{
  "mode": "architecture-summary",
  "status": "completed | failed",
  "system_overview": "string — 2-3 sentence system description",
  "components": [
    {
      "name": "string",
      "purpose": "string",
      "key_files": ["string"],
      "dependencies": ["string — other components this depends on"]
    }
  ],
  "mermaid_source": "string — Mermaid diagram source for component relationships",
  "data_flow": "string — description of main data flow through the system",
  "key_decisions": [
    {
      "decision": "string",
      "rationale": "string"
    }
  ],
  "dependencies": [
    {
      "name": "string — library/framework name",
      "purpose": "string — why it was chosen"
    }
  ],
  "complexity_hotspots": ["string — files with highest complexity from graph"],
  "notes": "string"
}
