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
| `/init` | Analyze the current project and draft an `ALPHA.md` in the working directory. Pass `/init --force` to overwrite an existing one. |
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

> **Note on `cp` and `mv`**: these used to be auto-approved by default,
> but the auto-approve path doesn't validate the source path — so the
> agent could `cp /etc/passwd /tmp/leak` without a prompt. They were
> removed from the built-in safe list. If you actually rely on them,
> add an `allow` rule:
>
> ```json
> "allow": ["execute_shell:^(cp|mv) "]
> ```

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

> **⚠ Security note — hooks see your secrets.** Hook commands inherit your
> entire `os.environ`, including `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, AWS
> credentials, GitHub tokens, and anything else in `.env`. This is by design
> — hooks need env vars to do useful work — but it means **a hook command
> that pipes `env` to a remote endpoint will leak everything**. Treat the
> `.alpha/settings.json` config as if it were source code:
>
> - Only paste hook commands from sources you trust (your own scripts, your
>   own audited snippets — not from random tutorials).
> - Avoid `command: "curl ... -d \"$(env)\""` style hooks unless you really
>   know the endpoint. The agent will run them.
> - In shared environments (team dotfiles, devcontainers), review hooks on
>   first checkout — same care you'd give to a `.bashrc` you didn't write.
>
> Tool calls from the agent itself go through `safe_env` (strips API keys
> before subprocess); hooks **do not** — they receive raw `os.environ`.

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

## 6. Per-project context (`ALPHA.md`)

Drop an `ALPHA.md` at your project root and Alpha auto-injects it into the
system prompt at startup. Use it to teach Alpha project-specific
conventions, layout, and gotchas — the equivalent of the `CLAUDE.md` you
might already have for Claude Code, but read by Alpha exclusively.

```markdown
# ALPHA.md

## House rules
- Python 3.11+, type hints on new code.
- Tests live in tests/, run with pytest.

## Status
Active sprint state lives in docs/STATUS.md.
```

### How resolution works

- Alpha walks **up** from the current directory until it finds an
  `ALPHA.md` (same as how git finds `.git`). First match wins.
- File is capped at **16 KB**; larger files are truncated with a notice
  so the agent knows context is missing.
- Alpha **only** reads `ALPHA.md` — not `CLAUDE.md` or `AGENTS.md` —
  because those files are written for a different agent's identity.
- The banner shows what got loaded:
  ```
  → Project context: ALPHA.md (2.9 KB)
  ```

### Opt-out

Set `ALPHA_NO_PROJECT_CONTEXT=1` to skip the lookup. Useful when
debugging prompt issues or running in CI where you want a clean prompt.

```bash
ALPHA_NO_PROJECT_CONTEXT=1 python main.py
```

### What to put in `ALPHA.md`

Stable project-level guidance — convention, conventions, dependencies,
identity, "don't do X here". For *running state* (sprint progress, open
issues, recent fixes) use `docs/STATUS.md` instead — that file rotates
quickly and bloats the system prompt.

---

## 7. Skills — creating, sharing, hiding

Skills are markdown playbooks (`SKILL.md`) the agent loads on demand.
Browse them with `/skills`, invoke with `/<skill-name> [args]`.

### Where they live

The Alpha registry searches **two** paths and merges what it finds:

| Path | In git? | Use for |
|---|---|---|
| `<project>/skills/<name>/SKILL.md` | **Yes** — tracked unless you ignore it | Skills that ship with the project (deploy, lint, project workflows). The team gets them on `git pull`. |
| `~/.alpha/skills/<name>/SKILL.md` | **No** — outside the repo | Personal shortcuts, skills with embedded tokens, anything you don't want shared. |

Both paths are loaded the same way at startup. The skill name must be
unique across both — if a name collides, the project copy wins.

### Anatomy

```markdown
---
name: my-skill
description: Use when the user asks for X or says "do Y" or "fix Z".
metadata:
  alpha:
    emoji: "🔧"
    requires:
      bins: [tool1, tool2]    # optional — checked at /skills time
---

# My Skill

Free-form markdown. Steps, commands, gotchas.
```

The `description` is what the model uses to decide when to load the
skill on its own (without `/skill-name`). Write it as a trigger sentence.

### Picking a location

| Scenario | Where |
|---|---|
| Project deploy / build / domain workflow | `<project>/skills/` |
| Skill with API tokens or private credentials | `~/.alpha/skills/` |
| Personal shortcut you use across many projects | `~/.alpha/skills/` |
| Experimental — not ready to publish | `~/.alpha/skills/`, move to `<project>/skills/` once stable |

### Don't commit secrets

Skills under `<project>/skills/` go to git like any other file. If your
SKILL.md inlines an API key, token, or password, **it ends up in the
repo history**. Two safer options:

1. Put the skill in `~/.alpha/skills/` so it never enters git.
2. Reference an env var in the body (`${MY_TOKEN}`) and load the value
   from `.env` (which is already in `.gitignore`).

### Moving between locations

```bash
# Personal → project (publish to the team)
mv ~/.alpha/skills/my-skill skills/
git add skills/my-skill && git commit -m "feat(skills): add my-skill"

# Project → personal (unpublish)
git rm -r skills/my-skill
mv skills/my-skill ~/.alpha/skills/   # if you kept a copy
```

### Authoring with Alpha itself

Easiest way to scaffold a new skill:

```
> /skill-creator quero criar uma skill chamada deploy-prod
                 que faz git pull + npm build + pm2 restart
```

`skill-creator` (the bundled meta-skill) walks the agent through
frontmatter, description triggers, body structure, and validation.

---

## 8. Approval flow

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

## 9. Troubleshooting

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

## 10. Tips

- Drop a project-level `CLAUDE.md` (or any file containing project context)
  and reference it in your prompt: *"siga o que o CLAUDE.md diz"*.
- Use `/save` before risky tasks; `/load` lets you roll back.
- Pipe one-shot output: `python main.py "lista os imports de main.py" > out.md`.
- For long sessions, `/history` then copy-paste relevant turns into a fresh
  `/clear`-ed session — costs less and stays focused.
