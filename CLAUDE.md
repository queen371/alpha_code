# CLAUDE.md

Architecture and module-level orientation for Claude Code working in this
repo. **For other audiences:**

- **End users** → `docs/USER_GUIDE.md` (install, REPL commands, settings).
- **Public README** → `README.md`.
- **Behavior rules for the Alpha runtime itself** → `ALPHA.md` (auto-loaded
  by Alpha; covers what to do / not do when editing here).

This file deliberately avoids re-stating install/run instructions or
configuration syntax — those live in the docs above. What's here is the
internal architecture you need to navigate the codebase.

## Architecture overview

The agent runs an async generator loop:

```
LLM call → tool detection → approval gate → tool execution → repeat
```

Max 50 iterations per task. Events are yielded to the CLI for real-time
streaming display.

### Core flow

```
main.py
  → agent.run_agent() (async generator)
    → llm.stream_chat_with_tools()
    → executor.execute_tool_calls()
  ← yields {token, tool_call, tool_result, approval_needed, done, error}
```

### Module map

| Module | Responsibility |
|---|---|
| `alpha/agent.py` | Orchestrates the loop. Loop detection (exact / fuzzy / cycle / stale), context compression trigger, iteration limits. |
| `alpha/llm.py` | SSE streaming client for OpenAI-compatible APIs. Incremental tool-call accumulation. Retry with jittered backoff. Shared `httpx.AsyncClient` with per-loop recreation. |
| `alpha/llm_anthropic.py` | Anthropic API adapter (selected via `api_format: "anthropic"` in provider config). |
| `alpha/executor.py` | Runs tool calls. Single calls run sequentially; multi-call batches fan out approved tools in parallel via `asyncio.gather`. Hard-blocks denied tools, runs `pre_tool` hooks (parallelized) with veto, executes, fires `post_tool`. Timeouts: 120s default, 300s for slow tools (`_SLOW_TOOLS`); truncates results > 12K chars. |
| `alpha/approval.py` | Two-tier safety: built-in defaults (auto-approve list, shell allowlist of 75+ commands with operator blacklist `;`, `&`, backticks, `$()`) plus user-defined `allow`/`deny` rules from `.alpha/settings.json`. `is_denied(name, args)` is a hard-block (no prompt). `needs_approval(name, args)` decides between auto-execute and prompt. |
| `alpha/context.py` | Adaptive compression via LLM summarization. `_compress_consecutive_failures` is a `ContextVar` (isolates parent vs sub-agent retry budget). Hard-truncate fallback when compression keeps returning empty. |
| `alpha/config.py` | Provider configs (`_PROVIDERS`), feature flags (`FEATURES`), limits (`LIMITS`), system prompt loader. Owns `_PROJECT_ROOT`. |
| `alpha/project_context.py` | Walk-up resolver for `ALPHA.md`. 16 KB cap. Opt-out via `ALPHA_NO_PROJECT_CONTEXT=1`. |
| `alpha/settings.py` | Shared loader for `.alpha/<file>` config files. `find_config_file(name)` resolves CWD → project root → home; `read_json(path, default)` reads-and-tolerates. |
| `alpha/hooks.py` | Declarative hooks from `.alpha/settings.json`. `pre_tool` can veto with `blocking: true`. Payload via stdin (JSON) and env vars (`ALPHA_TOOL_NAME`, `ALPHA_TOOL_ARGS_JSON`, etc.). |
| `alpha/mcp/` | Stdio MCP client. JSON-RPC handshake, tool discovery, registration as `mcp__<server>__<tool>`. Sync `subprocess.Popen` + reader thread because the CLI recreates an event loop per turn. |
| `alpha/display.py` | ANSI terminal UI. Approval prompts in Portuguese. Special rendering for `present_plan` (yellow card) and `todo_write` (checklist). |
| `alpha/repl_input.py` | Rich line editor on `prompt_toolkit`. Image clipboard handling. Slash-command autocomplete (built-ins + skills). |
| `alpha/history.py` | Session persistence. Path-traversal guard via regex + `is_relative_to`. |
| `alpha/skills/` | Skill registry (auto-discovers `*/SKILL.md` under `<repo>/skills/` and `~/.alpha/skills/`). |
| `alpha/agents/` | Named-agent profiles with tool/skill filtering and prompt extras. |

## Tool system

Tools are `ToolDefinition` dataclasses (`alpha/tools/__init__.py`):

- `name`, `description`, JSON schema parameters.
- `safety`: `SAFE` (auto-run) or `DESTRUCTIVE` (gate by `needs_approval`).
- `executor`: async callable.
- `category`: `ToolCategory` enum or string literal.

**Registration**: `load_all_tools()` auto-discovers `*_tools.py` under
`alpha/tools/` plus optional `plugins/`. MCP tools are appended *after*
the built-ins so a misbehaving server can't shadow native tools.

**Dispatch**: `get_openai_tools()` returns the registry in OpenAI
function-calling format. `get_tool(name)` retrieves a `ToolDefinition`.

### Composite tools (`alpha/tools/composite_tools.py`)

`project_overview`, `run_tests`, `search_and_replace`, `deploy_check`
are macro-tools that orchestrate sub-tools via `_run_tool`. **Trust
model (#D110)**: `_run_tool` skips the approval gate — the composite
itself was already approved. Composites that touch state are marked
`DESTRUCTIVE`. Errors flow through `_annotate_error` so the model sees
the same `{ok: false, category}` invariant the executor enforces.

## Planning tools

- `present_plan(summary, steps)` — `DESTRUCTIVE`, gates execution behind
  user approval. Used at the start of any task with 3+ distinct steps.
- `todo_write(todos)` — `SAFE`, replaces (not appends) the full todo list.
  Statuses: `pending`, `in_progress`, `completed`, `cancelled`.

## Multi-agent delegation

`multi_agent_enabled` and `delegate_tool_enabled` are ON by default. The
agent exposes:

- `delegate_task` — spawn one focused sub-agent.
- `delegate_parallel` — up to `max_parallel_agents` (default 3) concurrent.

Each sub-agent gets an isolated **scratch directory** at
`{AGENT_WORKSPACE}/.alpha/runs/{agent-id}/`. Sub-agents read anywhere
under the parent workspace but should write artifacts to their scratch.

Workspace path enforcement is in `alpha/agents/workspace.py`
(`validate_workspace_args`). Tools listed in `PATH_PARAMS_BY_TOOL` have
their path args resolved against the active workspace; absolute paths
outside are rejected.

Sub-agents are blocked from calling `delegate_task` / `delegate_parallel`
(no recursive delegation) and from destructive tools when no approval
callback is wired.

## Loop detection (`alpha/agent.py`)

Four detectors, in order, after `_LOOP_DETECT_MIN_ITER` (3) iterations:

1. Exact repetition — same call signature `>= _MAX_REPEAT_CALLS` times.
2. Similar calls — same tool name + args fuzzy-match (`>= 0.92` ratio
   after stripping common prefix from path-like args). `>= 5` similar.
3. Cycle — `A→B→A→B` over a 20-call window.
4. Stale progress — last 6 tool results all very similar.

When a loop fires, the agent appends a synthetic user message
(`[ALPHA SYSTEM NOTE] Loop detected ...`) and forces a final response
without tool access. The assistant's content from the looping turn is
preserved in history; orphan `tool_calls` would crash the next provider
turn with HTTP 400.

## CI

`.github/workflows/ci.yml` runs `pytest` on Python 3.11 + 3.12 on every
PR and push to master. Browser tests are gated by Playwright availability.
