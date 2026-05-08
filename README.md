# Alpha Code

Standalone terminal agent that connects to multiple LLM providers (DeepSeek, OpenAI, Anthropic, Grok, Ollama) and executes tasks autonomously through a tool-calling loop.

Pure async Python, minimal dependencies (`httpx`, `python-dotenv`, `ddgs`, `pyyaml`, `prompt_toolkit`).

## Features

- **Multi-provider** — switch between DeepSeek, OpenAI, Anthropic, Grok, Ollama via env or `--provider` flag.
- **Adaptive context compression** — multi-pass LLM-driven summarization with shrinking protected tail; auto-recovers from context-window overflow.
- **Image attachments** — paste screenshots directly in the REPL (`Ctrl+V`/`Alt+V`) or attach via `/image <path>`.
- **MCP support** — connect external Model Context Protocol servers via `.alpha/mcp.json`.
- **Hooks & permissions** — declarative `pre_tool` / `post_tool` / `on_user_prompt` / `on_stop` hooks plus per-tool `allow`/`deny` rules in `.alpha/settings.json`.
- **Multi-agent delegation** — `delegate_task` and `delegate_parallel` for fanning out focused sub-agents with isolated workspaces.
- **Plan & todos** — built-in `present_plan` (gates execution behind approval) and `todo_write` tools.

## Install

```bash
git clone <your-repo-url>/Alpha_Code.git
cd Alpha_Code
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # then fill in your API keys
```

Requires Python ≥ 3.11. For image clipboard paste on Linux, install `xclip` (X11) or `wl-clipboard` (Wayland).

Optional extras:

```bash
pip install -e ".[browser]"   # adds Playwright tools
pip install -e ".[dev]"       # adds pytest
```

## Update

Easiest path — the bundled `alpha-update` script handles pull + reinstall + diff of `.env.example`:

```bash
alpha-update              # jumps to latest master
alpha-update v1.2.0       # pins to a specific release tag
```

Restart the REPL afterwards (`/exit` then `alpha` again).

Manual equivalent if you prefer:

```bash
cd Alpha_Code
git pull origin master
source .venv/bin/activate
pip install -e . --upgrade
```

If `.env.example` gained new variables, run `diff .env.example .env` to spot what to copy over.

To pin to a specific release tag instead of tracking `master`:

```bash
git fetch --tags
git checkout v1.2.0          # any tag from `git tag -l "v*"`
pip install -e . --upgrade
```

Run `git checkout master && git pull` to return to the rolling latest.

## Run

```bash
# Interactive REPL
python main.py

# Single-shot task
python main.py "your task"

# Provider override
python main.py --provider anthropic "your task"
python main.py --list-providers

# Wrapper that activates the venv automatically
./bin/alpha "your task"
```

## Configuration

| File | Purpose |
|------|---------|
| `.env` | API keys per provider, default provider, workspace root |
| `.alpha/settings.json` | Permission rules (`allow`/`deny`), hooks |
| `.alpha/mcp.json` | MCP server definitions |
| `agents/<name>/agent.yaml` | Named agent profiles (model, tools, workspace) |
| `prompts/system.md` | Top-level agent system prompt |

`.example` templates ship in `.alpha/` and `.env.example`.

## REPL commands

```
/help        Show command help
/tools       List available tools
/mcp         List connected MCP servers
/agents      List named agents
/agent       Switch active agent
/model       Switch provider/model
/image PATH  Attach an image (Ctrl+V also works)
/clear       Clear conversation history
/sessions    List saved sessions
```

## Architecture & internals

See [`CLAUDE.md`](./CLAUDE.md) for the agent loop, tool registration, MCP integration, hook payloads, and module layout.

## License

[MIT](./LICENSE)
