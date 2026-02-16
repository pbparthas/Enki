# Cross-Machine Sync

Enki stores all state locally under `~/.enki/`. To use the same
knowledge base across machines, you need to sync this directory.

## What Syncs

| File | Purpose | Priority |
|------|---------|----------|
| `db/wisdom.db` | Permanent knowledge (beads) | Required |
| `db/abzu.db` | Staging candidates, session summaries | Recommended |
| `config/enki.toml` | Configuration | Recommended |
| `persona/PERSONA.md` | AI personality | Optional |
| `projects/*/em.db` | Per-project orchestration state | Optional (can be large) |

`db/uru.db` (enforcement logs) is machine-local and does not need syncing.

## Option 1: Dotfiles Repo (Recommended)

Turn `~/.enki/` into a git repo:

    cd ~/.enki
    git init
    git add db/wisdom.db db/abzu.db config/ persona/
    git commit -m "Initial Enki sync"
    git remote add origin git@github.com:you/enki-brain.git
    git push -u origin main

On your other machine:

    git clone git@github.com:you/enki-brain.git ~/.enki
    enki setup    # Re-registers hooks and MCP server

Pull before each session, push after:

    cd ~/.enki && git pull
    # ... work ...
    cd ~/.enki && git add -A && git commit -m "session update" && git push

## Option 2: Manual Copy

Copy the database files directly:

    scp ~/.enki/db/wisdom.db user@other-machine:~/.enki/db/
    scp ~/.enki/db/abzu.db user@other-machine:~/.enki/db/
    scp ~/.enki/config/enki.toml user@other-machine:~/.enki/config/

Or use rsync for incremental sync:

    rsync -avz ~/.enki/ user@other-machine:~/.enki/ \
      --exclude uru.db \
      --exclude projects/

## After Syncing to a New Machine

Run `enki setup` on the new machine to:
- Re-register hooks in `~/.claude/hooks/`
- Re-register MCP server in `~/.claude/settings.json`
- Ensure all directories exist

## Conflict Resolution

SQLite databases use WAL mode, which makes them safe for
single-writer scenarios. If both machines modify the same database:

1. Pick one machine's version as the source of truth
2. The other machine's recent beads can be re-extracted from
   session transcripts using `enki session end`
3. Staged candidates (abzu.db) that are lost can be re-captured
   on the next session

Avoid simultaneous writes to the same database from different machines.
SQLite is not designed for multi-writer concurrent access.

## Automation

For frequent syncing, add to your shell profile:

    alias enki-pull="cd ~/.enki && git pull"
    alias enki-push="cd ~/.enki && git add -A && git commit -m 'sync' && git push"
