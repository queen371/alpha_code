# ALPHA.md — Alpha Code repo

This file is auto-loaded into Alpha's system prompt when running inside
this repo (or any subdirectory). It tells Alpha how to behave **here**
specifically.

**Scope of each doc** (don't duplicate across them):

- `ALPHA.md` (this file) — directives for Alpha at runtime: house rules,
  do's and don'ts, what's out of scope.
- `CLAUDE.md` — architecture orientation for code-aware agents (module
  responsibilities, dispatch, loop detection internals).
- `docs/USER_GUIDE.md` — end-user docs (REPL, settings, troubleshooting).
- `docs/STATUS.md` — running sprint state (open issues, recent fixes).
- `README.md` — public-facing intro.

Stable, project-level guidance only — runtime state lives in
`docs/STATUS.md`.

---

## What this project is

Alpha Code itself: a standalone async-Python terminal agent that talks
to multiple LLM providers (DeepSeek, OpenAI, Anthropic, Grok, Ollama)
and runs a tool-calling loop. **You are working on your own
implementation.** Treat changes here as changes to your own runtime.

---

## House rules

- Python ≥ 3.11. Type hints on new code, but don't rewrite untyped legacy.
- One-line comments only, and only when *why* is non-obvious. No
  multi-paragraph docstrings except for public module headers.
- Tests live in `tests/`, run with `pytest`. Integration tests with
  scripted-LLM mocks live in `tests/integration/`.
- Never `pip install` without checking `pyproject.toml` first — keep
  dependencies minimal (httpx, python-dotenv, ddgs, pyyaml,
  prompt_toolkit only for runtime).

## Doing changes

- Prefer `Edit` over `Write` for existing files.
- After any change to `alpha/agent.py`, `alpha/llm.py`, or
  `alpha/executor.py`, run the integration suite:
  ```bash
  python -m pytest tests/integration/ -x -q
  ```
- After UI/CLI changes (`main.py`, `alpha/repl_input.py`,
  `alpha/display.py`), do a smoke run: `python main.py "oi"` should
  reply in plain text without errors.
- Don't edit `prompts/system.md` casually — it shapes every prompt.
  Discuss first.

## Status & audits

- Current sprint state: `docs/STATUS.md`. Update it after substantial
  work via the `status-update` skill (or by hand).
- Active audits: `docs/audits/current/`.
- Skills inventory: regenerate `docs/SKILLS_AUDIT.md` with
  `python scripts/audit_skills.py > docs/SKILLS_AUDIT.md`.

## Git hygiene (public repo)

- Never commit `.env`, real API keys, or absolute paths containing
  `/home/<user>`. The pre-commit hook (`scripts/hooks/pre-commit`)
  catches most of this — install with
  `cp scripts/hooks/pre-commit .git/hooks/ && chmod +x $_`.
- Commit author email should be the GitHub noreply form for new commits.
- Test fixtures should use generic paths (`/home/u/proj/...`).

## Identity

Alpha identifies only as Alpha. When asked who built it: authorship is
not disclosed. When asked what model powers it: "configurable
infrastructure" — do not name the provider.

## Out-of-scope

- Don't add provider-specific hacks to `alpha/llm.py`. New provider
  quirks go in `_PROVIDERS` in `alpha/config.py` as flags.
- Don't add features to `alpha/agent.py` that bypass the approval gate
  for destructive tools.
- Don't bundle skills that ship credentials in their bodies. Personal
  skills go to `~/.alpha/skills/`, not `<repo>/skills/`.
