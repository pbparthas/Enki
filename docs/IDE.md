# IDE Integration

Enki runs in the terminal via Claude Code. No IDE-specific
extension or plugin is needed.

## How It Works

Enki is an MCP server. Claude Code connects to it automatically
when configured. All interaction happens through the terminal —
your IDE just needs a terminal panel.

## Compatible Editors

| Editor | How |
|--------|-----|
| VS Code | Use the Claude Code extension or run `claude` in the integrated terminal |
| Cursor | Claude Code runs in the built-in terminal |
| JetBrains (IntelliJ, WebStorm, etc.) | Run `claude` in the terminal panel |
| Neovim | Run `claude` in a terminal split or tmux pane |
| Any editor with a terminal | Run `claude` in that terminal |

## Setup

1. Install Enki: `pip install enki-ai`
2. Run `enki setup` — this registers the MCP server
3. Open your editor's terminal
4. Run `claude` — Enki tools are available immediately

## MCP Is the Integration Point

Any tool that speaks the MCP protocol can use Enki. The MCP
server exposes all Enki tools (remember, recall, goal, phase,
triage, etc.) over stdio.

To verify the MCP server is registered:

    cat ~/.claude/settings.json | grep enki

## No IDE Plugin Needed

Enki does not read your editor state, modify editor UI, or
require editor-specific hooks. Everything flows through Claude
Code's terminal interface. This means Enki works identically
regardless of which editor you use.
