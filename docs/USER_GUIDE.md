# Alpha Code — User Guide

A practical walkthrough for running and configuring Alpha as a daily driver.
For internal architecture and contribution notes, see `CLAUDE.md`.

---

## 1. Install

Requires Python 3.11+.

```bash
git clone <your repo>
cd Alpha_Code
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Open `.env` and fill the API keys for the providers you want to use:

```ini
# Pick at least one. Ollama needs none — runs locally.
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GROK_API_KEY=xai-...
```

The default provider is **deepseek**. Override with `ALPHA_PROVIDER=openai` in
`.env` or with `--provider <name>` on the command line.

### Optional: shell wrapper

```bash
./bin/alpha "your task"
```

The wrapper auto-activates `.venv` so you can call Alpha from anywhere.

---

## 2. First run

```bash
python main.py
```

You should see the green ALPHA CODE banner. Try a few prompts:

- `oi` — chat reply, no tool calls.
- `liste os arquivos do diretório atual` — runs `list_directory`.
- `analise este projeto` — runs project_overview, reads key files, summarizes.
- `crie um arquivo hello.txt com "oi mundo"` — runs `write_file`.

For one-shot (no REPL):

```bash
python main.py "explique o pyproject.toml"
```

---

## 3. Built-in commands (REPL)

Type these directly at the prompt. They don't go to the LLM.

| Command | What it does |
|---|---|
| `/help` | Show all commands. |
| `/clear` | Wipe conversation history and screen. |
| `/history` | Print the message history. |
| `/save` / `/load` / `/sessions` | Persist and restore sessions. |
| `/continue` | Resume from the last session. |
| `/tools` | List every tool available to the agent. |
| `/skills` | List every registered skill, split into ready and inactive (missing bins). |
| `/mcp` | List connected MCP servers. |
| `/model` | Show or switch provider/model. |
| `/agents` / `/agent` | List or switch named agent profiles. |
| `/image` | Attach an image to the next message (Ctrl+V also works). |
| `/<skill-name>` | Invoke a skill directly. The skill's instructions are inlined into the next message. Optional args follow the name: `/skill-creator make a deploy skill`. |
| `/exit` | Quit. |

---

## 4. Providers and models

Available providers (set with `--provider` or `ALPHA_PROVIDER`):

| Provider | Default model | Needs API key |
|---|---|---|
| `deepseek` | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| `openai` | `gpt-4o` | `OPENAI_API_KEY` |
| `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| `grok` | `grok-4-1-fast-reasoning` | `GROK_API_KEY` |
| `ollama` | `qwen-heavy-abliterated:32b` | none (local) |
| `gemma-12b` / `gemma-27b` | gemma3 via Ollama | none (local) |

Override the model per-provider via env:

```bash
DEEPSEEK_MODEL=deepseek-chat python main.py
```

List all providers and which keys are configured:

```bash
python main.py --list-providers
```

Switch on the fly inside the REPL with `/model`.

---

## 5. Configuration: `.alpha/settings.json`

Drop this file in your project (or `~/.alpha/settings.json` for a global
default) to control what runs automatically and what hooks fire on tool calls.

Resolution order, first match wins:

```
./.alpha/settings.json   →   <project_root>/.alpha/settings.json   →   ~/.alpha/settings.json
```

### Permissions: allow / deny

```json
{
  "permissions": {
    "allow": [
      "read_file",
      "list_directory",
      "execute_shell:^npm (test|run|ci|install)"
    ],
    "deny": [
      "execute_shell:rm -rf",
      "execute_shell:sudo",
      "execute_shell:curl.*\\|.*sh"
    ]
  }
}
```

Rule syntax:

- `tool` — match any args.
- `tool(literal)` — match the primary arg exactly.
- `tool:regex` — regex search against the primary arg.

`deny` rules are a hard block: the agent cannot run them, no prompt shown.
`allow` rules override the built-in approval policy and auto-execute.

### Hooks

Hooks run shell commands at lifecycle events. Each hook receives the tool
payload via env vars (`ALPHA_TOOL_NAME`, `ALPHA_TOOL_ARGS_JSON`,
`ALPHA_USER_PROMPT`, `ALPHA_WORKSPACE`) and stdin (JSON).

```json
{
  "hooks": {
    "pre_tool": [
      {
        "matcher": "write_file|edit_file",
        "command": "ruff check ${ALPHA_TOOL_ARGS_JSON} || true",
        "blocking": false
      }
    ],
    "post_tool": [
      {"matcher": "write_file", "command": "echo wrote >> /tmp/audit.log"}
    ],
    "on_user_prompt": [
      {"command": "echo \"$ALPHA_USER_PROMPT\" >> /tmp/prompts.log"}
    ],
    "on_stop": [
      {"command": "notify-send 'Alpha done' || true"}
    ]
  }
}
```

- `pre_tool` with `"blocking": true` can **veto** a tool call (non-zero exit
  cancels the call).
- `post_tool` is informational only.
- `matcher` is optional regex; omit to fire for every tool.

A complete starter is shipped at `.alpha/settings.json.example`. Copy it:

```bash
cp .alpha/settings.json.example .alpha/settings.json
```

### MCP servers

External Model Context Protocol servers are configured separately in
`.alpha/mcp.json`:

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

Their tools appear as `mcp__<server>__<tool>` and require approval by
default. Add an `allow` rule above to auto-approve specific ones.

---

## 6. Approval flow

When the agent calls a `DESTRUCTIVE` tool that isn't in your `allow` list,
you'll see:

```
  ┌─ Approval needed
  │ Tool: execute_shell
  │ command: rm /tmp/scratch.txt
  └ Aprovar? [s/n/a]
```

- `s` — approve once.
- `n` — deny this call.
- `a` — approve all calls in this session.

Read-only tools (`read_file`, `list_directory`, `glob_files`, etc.) run
automatically without prompts.

---

## 7. Troubleshooting

**`API key not set for <provider>`** — The `*_API_KEY` env var is missing.
Check `.env` is in the project root and `pip install -e .` was run with
`python-dotenv` available.

**The agent says "Loop detected" and stops early** — It detected repeated
similar tool calls. If this fires on a legitimate exploration, file a bug
with the call sequence. Configurable thresholds live in `alpha/agent.py`
(`_MAX_REPEAT_CALLS`, `_SIMILAR_REPEAT_CALLS`, `_LOOP_DETECT_MIN_ITER`).

**`HTTP 400` on the next prompt after Ctrl+C** — Interrupting mid-tool
leaves orphan `tool_calls` in history. Run `/clear` to start fresh, or
`/continue` to resume from a saved session before the break.

**Provider returns "context overflow"** — Alpha auto-compresses history via
LLM summarization. If it still overflows, run `/clear` between long tasks.

**Ollama: tools aren't being called** — Smaller local models often hallucinate
or skip tool calls. The provider config sets `low_temperature: True` and
clamps temperature to 0.2 to mitigate. If it still misbehaves, switch to a
larger model or use `--provider deepseek`.

**`Failed to load skill: Missing YAML frontmatter`** — A skill's `SKILL.md`
lacks the `---` frontmatter block. See `docs/SKILLS_AUDIT.md` for the
current state of every skill (complete / inactive / broken).

---

## 8. Tips

- Drop a project-level `CLAUDE.md` (or any file containing project context)
  and reference it in your prompt: *"siga o que o CLAUDE.md diz"*.
- Use `/save` before risky tasks; `/load` lets you roll back.
- Pipe one-shot output: `python main.py "lista os imports de main.py" > out.md`.
- For long sessions, `/history` then copy-paste relevant turns into a fresh
  `/clear`-ed session — costs less and stays focused.
