# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Alpha Code

A standalone terminal agent that connects to multiple LLM providers (DeepSeek, OpenAI, Anthropic/Claude, Grok, Ollama) and executes tasks autonomously using a tool-calling loop. Written in pure async Python with minimal dependencies (httpx, python-dotenv, ddgs, pyyaml, prompt_toolkit).

## Running

```bash
# Interactive REPL
python main.py

# Single-shot
python main.py "your task"

# Provider override
python main.py --provider openai "your task"
python main.py --list-providers

# Via wrapper (requires .venv)
./bin/alpha "your task"
```

## Dependencies & Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # then fill in API keys
```

Requires Python >= 3.11. Tests in `tests/` run with `pytest` (declared in `pyproject.toml`).

## Architecture

The agent runs an async generator loop: **LLM call → tool detection → approval gate → tool execution → repeat** (max 50 iterations). Events are yielded to the CLI for real-time streaming display.

### Core loop flow

`main.py` → `agent.run_agent()` (async generator) → `llm.stream_chat_with_tools()` → `executor.execute_tool_calls()` → yields events (`token`, `tool_call`, `tool_result`, `approval_needed`, `done`, `error`)

### Key modules

- **`alpha/agent.py`** — Orchestrates the loop. Handles loop detection (exact/fuzzy/cycle/stale), context compression via LLM summarization, and iteration limits.
- **`alpha/llm.py`** — SSE streaming client for OpenAI-compatible APIs. Accumulates tool calls incrementally from deltas. Retry with jittered exponential backoff.
- **`alpha/executor.py`** — Runs tool calls. Single calls run sequentially; multiple calls fan out approved ones in parallel. Hard-blocks denied tools, runs `pre_tool` hooks (parallelized) with veto, executes, then fires `post_tool` hooks. Timeouts: 120s standard, 300s for slow tools; truncates results > 20K chars.
- **`alpha/config.py`** — Provider configs (base_url, api_key_env, model). Loads system prompt from `prompts/system.md`. Owns `_PROJECT_ROOT`.
- **`alpha/settings.py`** — Shared loader for `.alpha/<file>` config files. `find_config_file(name)` resolves CWD → project root → home; `read_json(path, default)` reads-and-tolerates. Used by `approval`, `hooks`, `mcp/config`.
- **`alpha/approval.py`** — Two-tier safety: built-in defaults (auto-approve list, shell allowlist of 75+ commands with operator blacklist `;`, `&`, `` ` ``, `$()`) plus user-defined `allow`/`deny` rules from `.alpha/settings.json`. `is_denied(name, args)` is a hard-block (no prompt, no execution). `needs_approval(name, args)` decides between auto-execute and approval prompt.
- **`alpha/hooks.py`** — Declarative hooks from `.alpha/settings.json`. Events: `pre_tool` (can veto with `blocking: true`), `post_tool` (informational), `on_user_prompt`, `on_stop`. Optional regex `matcher` per hook. Payload to commands via stdin (JSON) and env vars (`ALPHA_TOOL_NAME`, `ALPHA_TOOL_ARGS_JSON`, `ALPHA_USER_PROMPT`, `ALPHA_WORKSPACE`).
- **`alpha/mcp/`** — Stdio MCP client. Spawns subprocesses, performs JSON-RPC handshake (`initialize` + `notifications/initialized`), discovers tools via `tools/list`, registers them as `mcp__<server>__<tool>` in `TOOL_REGISTRY` (all `DESTRUCTIVE` by default — gate via `allow` rules). Config in `.alpha/mcp.json` (`mcpServers` block, Claude-Code-compatible). Sync `subprocess.Popen` + reader thread because the CLI recreates an event loop per turn.
- **`alpha/display.py`** — ANSI terminal UI. Approval prompts are in Portuguese ("Aprovar? [s/n]"). Special rendering for `present_plan` (yellow plan card) and `todo_write` (checklist).
- **`alpha/repl_input.py`** — Rich line editor on top of `prompt_toolkit`. Returns `(text, image_paths)`. Ctrl+V (with Alt+V fallback) reads images from the system clipboard, saves them to `/tmp/alpha-clip-*.png`, and inserts a `[Image #N]` placeholder.
- **`alpha/clipboard.py`** — Image clipboard reader for X11 (`xclip`) and Wayland (`wl-paste`). Probes available types before reading to avoid blocking.
- **`alpha/attachments.py`** — Builds provider-neutral user content from text + image paths. Returns a plain string when no images are attached, or an OpenAI-shaped block list (`text` + `image_url`) otherwise. The Anthropic adapter translates that list to Anthropic's `image`/`source.base64` shape.

### Tool system

Tools are defined as `ToolDefinition` dataclasses with name, description, JSON schema parameters, safety level (SAFE/DESTRUCTIVE), async executor, and category.

**Registration**: `load_all_tools()` auto-discovers all `*_tools.py` files in `alpha/tools/` plus optional `plugins/` directory. Each module registers tools into a global `TOOL_REGISTRY` dict. MCP tools are appended to the same registry by `load_mcp_servers()` after the built-ins (so a misbehaving server can't shadow native tools).

**Dispatch**: `get_openai_tools()` returns the registry in OpenAI function-calling format. `get_tool(name)` retrieves a `ToolDefinition` for execution.

### Planning tools

- `present_plan(summary, steps)` — `DESTRUCTIVE`, gates execution behind user approval. Used at the start of any task with 3+ distinct steps; the user reviews/denies the plan before any modifying tool runs. Stateless: the plan itself lives in conversation history.
- `todo_write(todos)` — `SAFE`, replaces (not appends) the full todo list. Statuses: `pending`, `in_progress`, `completed`, `cancelled`. Display renders as a checklist with status glyphs.

### Provider system

All providers use the same OpenAI-compatible chat completions endpoint. Config in `_PROVIDERS` dict in `config.py`. Selected via `ALPHA_PROVIDER` env var or `--provider` CLI flag. API keys come from environment variables (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GROK_API_KEY`). Ollama needs no key.

### MCP integration

External Model Context Protocol servers are configured in `.alpha/mcp.json`:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
    }
  }
}
```

Servers are spawned at startup; their tools appear as `mcp__<server>__<tool>` and are marked `DESTRUCTIVE` (require approval) by default. Add an `allow` rule in `.alpha/settings.json` to auto-approve specific MCP tools. Use `/mcp` in the REPL to list connected servers. `${VAR}` env-var expansion is supported in `args` and `env`.

### Hooks and permissions (`.alpha/settings.json`)

```json
{
  "permissions": {
    "allow": ["read_file", "execute_shell:^npm (test|run)"],
    "deny":  ["execute_shell:rm -rf", "execute_shell:sudo"]
  },
  "hooks": {
    "pre_tool":  [{"matcher": "write_file|edit_file", "command": "ruff check ${ALPHA_TOOL_ARGS_JSON} || true", "blocking": false}],
    "post_tool": [{"matcher": "write_file", "command": "echo wrote >> /tmp/audit.log"}],
    "on_user_prompt": [{"command": "echo \"$ALPHA_USER_PROMPT\" >> /tmp/prompts.log"}],
    "on_stop":   [{"command": "notify-send 'Alpha done'"}]
  }
}
```

Permission rule syntax: `tool` (any args) | `tool(literal)` (exact primary-arg match) | `tool:regex` (primary-arg search). `deny` is enforced as a hard-block by the executor before any prompt; `allow` overrides built-in defaults to auto-approve.

Settings files are resolved in priority order (first match wins): `./.alpha/<file>` → `<project_root>/.alpha/<file>` → `~/.alpha/<file>`.

### Multi-agent delegation

`multi_agent_enabled` and `delegate_tool_enabled` are ON by default. The agent exposes two tools:

- `delegate_task` — spawn one sub-agent for a focused task
- `delegate_parallel` — spawn up to `max_parallel_agents` sub-agents (default 3) concurrently

Each sub-agent gets an isolated **scratch directory** at `{AGENT_WORKSPACE}/.alpha/runs/{agent-id}/` (id format: `YYYYMMDD-HHMMSS-<8hex>`). Sub-agents can read anywhere under the parent workspace but are expected to write artifacts to their scratch dir. On completion the parent receives a sorted `scratch_files` list of created paths.

Workspace path enforcement lives in `alpha/agents/workspace.py` (`validate_workspace_args`). Tools listed in `PATH_PARAMS_BY_TOOL` have their path args resolved against the active workspace; absolute paths outside are rejected.

Sub-agents are blocked from calling `delegate_task` / `delegate_parallel` (no recursive delegation) and from destructive tools when no approval callback is wired.

## Conventions

- System prompt lives in `prompts/system.md` — the agent's identity and behavioral directives.
- Sub-agent prompt lives in `prompts/subagent.md`.
- UI strings are in Portuguese (approval prompts, banner).
- Workspace defaults to CWD (`alpha/tools/workspace.py`).
- Static config flags live in `config.py` (`multi_agent_enabled`, `delegate_tool_enabled`); user-overridable behavior (permissions, hooks) lives in `.alpha/settings.json`; MCP servers in `.alpha/mcp.json`. `.example` templates ship in `.alpha/` for both.
- CI runs `pytest` on Python 3.11 + 3.12 via `.github/workflows/ci.yml` on every PR and push to master.
