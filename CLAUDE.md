# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Alpha Code

A standalone terminal agent that connects to OpenAI-compatible LLM APIs (DeepSeek, OpenAI, Grok, Ollama) and executes tasks autonomously using a tool-calling loop. Written in pure async Python with minimal dependencies (httpx, python-dotenv, duckduckgo-search).

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

Requires Python >= 3.11. No test framework is configured yet.

## Architecture

The agent runs an async generator loop: **LLM call → tool detection → approval gate → tool execution → repeat** (max 25 iterations). Events are yielded to the CLI for real-time streaming display.

### Core loop flow

`main.py` → `agent.run_agent()` (async generator) → `llm.stream_chat_with_tools()` → `executor.execute_tool_calls()` → yields events (`token`, `tool_call`, `tool_result`, `approval_needed`, `done`, `error`)

### Key modules

- **`alpha/agent.py`** — Orchestrates the loop. Handles loop detection (same tool call 3x → force text), message truncation (every 5 iterations), and iteration limits.
- **`alpha/llm.py`** — SSE streaming client for OpenAI-compatible APIs. Accumulates tool calls incrementally from deltas.
- **`alpha/executor.py`** — Runs tool calls sequentially. Checks approval, enforces timeouts (120s standard, 300s for slow tools), truncates results > 20K chars.
- **`alpha/config.py`** — Provider configs (base_url, api_key_env, model). Loads system prompt from `prompts/system.md`.
- **`alpha/approval.py`** — Rule-based safety: auto-approve safe tools, require approval for destructive ops. Shell commands validated against allowlist of 75+ commands with operator blacklist (`;`, `&`, `` ` ``, `$()`).
- **`alpha/display.py`** — ANSI terminal UI. Approval prompts are in Portuguese ("Aprovar? [s/n]").

### Tool system

Tools are defined as `ToolDefinition` dataclasses with name, description, JSON schema parameters, safety level (SAFE/DESTRUCTIVE), async executor, and category.

**Registration**: `load_all_tools()` auto-discovers all `*_tools.py` files in `alpha/tools/` plus optional `plugins/` directory. Each module registers tools into a global `TOOL_REGISTRY` dict.

**Dispatch**: `get_openai_tools()` returns the registry in OpenAI function-calling format. `get_tool(name)` retrieves a `ToolDefinition` for execution.

### Provider system

All providers use the same OpenAI-compatible chat completions endpoint. Config in `_PROVIDERS` dict in `config.py`. Selected via `ALPHA_PROVIDER` env var or `--provider` CLI flag. API keys come from environment variables (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GROK_API_KEY`). Ollama needs no key.

## Conventions

- System prompt lives in `prompts/system.md` — the agent's identity and behavioral directives.
- UI strings are in Portuguese (approval prompts, banner).
- Workspace defaults to CWD (`alpha/tools/workspace.py`).
- Feature flags in `config.py` are all currently disabled (sandbox, multi_agent, delegate).
