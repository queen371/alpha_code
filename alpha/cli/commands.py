"""Slash-command dispatch for the REPL.

Replaces the 400+ line ``if cmd == "/X" elif cmd == "/Y" ...`` chain that
used to live in ``main.py`` with a dict-based dispatch table where each
command is a small handler function.

## Contract

Every handler takes ``(ctx, parts)`` and returns a ``DispatchResult``:

- ``CONTINUE``: command handled, REPL loop should ``continue``.
- ``BREAK``: command handled, REPL loop should ``break`` (e.g. ``/exit``).
- ``FALL_THROUGH``: command transformed the input; the REPL should
  proceed with ``ctx.user_input_override`` (and optionally
  ``ctx.image_paths_override``) as if the user had typed it normally.
  Used by ``/init``, ``/<skill>``, ``/image``.

``ReplContext`` carries the mutable state the handlers need to read or
update (``messages``, ``history``, ``session_id``, ``provider``, etc.).
Handlers mutate the context in place — there's no functional purity to
preserve here, the original behavior was full of ``messages[:] = [...]``
in-place rebinds.

## Why

Three wins over the inline ``if/elif`` chain:

1. **Each command is a focused unit.** Easier to read, change, test.
2. **Adding a command is local.** Add to ``DISPATCH`` and write a
   handler — no scrolling through 400 lines to find the right ``elif``.
3. **Test coverage** can target a single handler instead of driving the
   whole REPL loop.

The integration tests in ``tests/integration/test_repl_flow.py`` lock
in the user-visible behavior so this refactor is safe.
"""

from __future__ import annotations

import enum
import os
import shutil
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Callable

from alpha.agents import AgentScope, get_agent
from alpha.attachments import build_user_content
from alpha.config import get_available_providers, get_provider_config
from alpha.display import (
    C,
    c,
    print_banner,
    print_error,
    print_sessions_list,
    print_tools_list,
    reset_approve_all,
)
from alpha.history import (
    generate_session_id,
    get_last_session_id,
    list_sessions,
    load_session,
    load_session_summary,
    save_session,
)
from alpha.mcp import list_active_servers as list_mcp_servers
from alpha.skills import get_skill, list_skills

from .setup import build_system_prompt, get_tools_for_agent, list_agents


class DispatchResult(enum.Enum):
    """What the REPL loop should do after a handler runs."""

    CONTINUE = "continue"
    BREAK = "break"
    FALL_THROUGH = "fall_through"


@dataclass
class ReplContext:
    """Mutable state shared between the REPL loop and command handlers.

    Handlers mutate the relevant fields in place. Two ``*_override``
    fields exist to carry transformed input from FALL_THROUGH commands
    back to the REPL loop.
    """

    # Conversation state
    messages: list[dict]
    history: list[dict]
    session_id: str

    # Provider/agent state
    provider: str
    temperature: float
    cfg: dict[str, Any]
    system_prompt: str
    tools: list[dict]
    get_tool_fn: Callable | None
    active_agent: AgentScope | None

    # FALL_THROUGH outputs — set by /init, /<skill>, /image when they
    # transform the user's input before the LLM call.
    user_input_override: str | None = None
    image_paths_override: list[Path] | None = None
    skip_history_record: bool = field(default=False)
    history_record_override: str | None = None


# ─── Pure handlers ──────────────────────────────────────────────────


def _handle_exit(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    if len(ctx.messages) > 1:
        save_session(
            ctx.session_id,
            ctx.messages,
            {"provider": ctx.provider, "model": ctx.cfg["model"]},
        )
        print(f"  {c(C.GRAY, f'Session saved: {ctx.session_id}')}")
    print(c(C.GRAY, "Goodbye."))
    return DispatchResult.BREAK


def _handle_clear(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    ctx.history.clear()
    ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
    ctx.session_id = generate_session_id()
    reset_approve_all()
    os.system("clear" if os.name != "nt" else "cls")
    print_banner(ctx.provider, ctx.cfg["model"])
    return DispatchResult.CONTINUE


def _handle_history(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    if not ctx.history:
        print(c(C.GRAY, "  History is empty."))
    else:
        for msg in ctx.history[-20:]:
            role = msg["role"]
            content = msg["content"][:100]
            color = C.GREEN if role == "user" else C.CYAN
            print(f"  {c(color, role)}: {content}")
    return DispatchResult.CONTINUE


def _handle_save(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    save_session(
        ctx.session_id,
        ctx.messages,
        {"provider": ctx.provider, "model": ctx.cfg["model"]},
    )
    print(f"  {c(C.GREEN, f'Session saved: {ctx.session_id}')}")
    return DispatchResult.CONTINUE


def _handle_load(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    if len(parts) < 2:
        sessions = list_sessions(10)
        if not sessions:
            print(c(C.GRAY, "  No saved sessions."))
        else:
            print(f"  {c(C.CYAN, 'Recent sessions:')}")
            for s in sessions:
                print(
                    f"  {c(C.GREEN, s['session_id'])} "
                    f"({s['message_count']} msgs) "
                    f"{c(C.GRAY, s['preview'])}"
                )
            print(f"\n  {c(C.GRAY, 'Usage: /load <session_id>')}")
        return DispatchResult.CONTINUE

    loaded = load_session(parts[1])
    if loaded is None:
        print(c(C.RED, f"  Session not found: {parts[1]}"))
        return DispatchResult.CONTINUE

    ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
    ctx.messages.extend(loaded)
    ctx.history.clear()
    ctx.history.extend(m for m in loaded if m["role"] in ("user", "assistant"))

    # Default: new session_id so `/save` later doesn't overwrite the
    # source. `--inplace` opts into editing the original (#DL018).
    if len(parts) >= 3 and parts[2] == "--inplace":
        ctx.session_id = parts[1]
        print(
            f"  {c(C.GREEN, f'Loaded {len(loaded)} messages from {parts[1]} (in-place: saves overwrite)')}"
        )
    else:
        ctx.session_id = generate_session_id()
        print(
            f"  {c(C.GREEN, f'Loaded {len(loaded)} messages from {parts[1]} into new session {ctx.session_id}')}"
        )
        print(
            f"  {c(C.GRAY, '  (use /load <id> --inplace to overwrite the original instead)')}"
        )
    return DispatchResult.CONTINUE


def _handle_continue(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    last_id = get_last_session_id()
    if not last_id:
        print(c(C.GRAY, "  No previous session found."))
        return DispatchResult.CONTINUE

    summary = load_session_summary(last_id)
    if not summary:
        loaded = load_session(last_id)
        if loaded is None:
            print(c(C.RED, f"  Failed to load session: {last_id}"))
            return DispatchResult.CONTINUE
        ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
        ctx.messages.extend(loaded)
        ctx.history.clear()
        ctx.history.extend(m for m in loaded if m["role"] in ("user", "assistant"))
        print(f"  {c(C.GREEN, f'Resumed {len(loaded)} messages from {last_id}')}")
    else:
        ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
        ctx.messages.append(
            {
                "role": "user",
                "content": (
                    f"[CONTEXT FROM PREVIOUS SESSION {last_id}]\n\n"
                    f"{summary}\n\n"
                    "[End of previous context. Continue from here.]"
                ),
            }
        )
        ctx.messages.append(
            {
                "role": "assistant",
                "content": (
                    "Understood. I have the context from our previous session. "
                    "How would you like to continue?"
                ),
            }
        )
        ctx.history.clear()
        print(f"  {c(C.GREEN, f'Resumed with summary from {last_id}')}")
    ctx.session_id = generate_session_id()
    return DispatchResult.CONTINUE


def _handle_sessions(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    print_sessions_list(list_sessions(20))
    return DispatchResult.CONTINUE


def _handle_tools(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    print_tools_list(ctx.tools)
    return DispatchResult.CONTINUE


def _handle_skills(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    skills = sorted(list_skills(), key=lambda s: s.name)
    if not skills:
        print(c(C.GRAY, "  No skills registered."))
        return DispatchResult.CONTINUE

    ready: list = []
    inactive: list = []
    for s in skills:
        missing = [b for b in s.requires_bins if not shutil.which(b)]
        (inactive if missing else ready).append((s, missing))

    summary = (
        f"{len(skills)} skills registered "
        f"({len(ready)} ready, {len(inactive)} inactive)"
    )
    print(f"  {c(C.GRAY, summary)}")
    print(f"  {c(C.GRAY, 'Invoke with /<skill-name> [args]')}")
    print()
    if ready:
        print(f"  {c(C.GREEN + C.BOLD, 'Ready')}")
        for s, _ in ready:
            desc = (s.description or "").strip().split("\n", 1)[0]
            print(
                f"  {c(C.GREEN, '✦')} {c(C.CYAN, s.name):<24} "
                f"{c(C.GRAY, desc[:90])}"
            )
        print()
    if inactive:
        print(f"  {c(C.YELLOW + C.BOLD, 'Inactive (missing bins)')}")
        for s, missing in inactive:
            print(
                f"  {c(C.YELLOW, '○')} {c(C.GRAY, s.name):<24} "
                f"{c(C.GRAY, 'needs: ' + ', '.join(missing))}"
            )
    return DispatchResult.CONTINUE


def _handle_mcp(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    servers = list_mcp_servers()
    if not servers:
        print(c(C.GRAY, "  No MCP servers connected. Configure .alpha/mcp.json"))
    else:
        for s in servers:
            tool_names = ", ".join(s["tools"]) or c(C.GRAY, "(no tools)")
            print(f"  {c(C.CYAN, s['name']):30s} {tool_names}")
    return DispatchResult.CONTINUE


def _handle_agents(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    agents = list_agents()
    if not agents:
        print(c(C.GRAY, "  No agents defined. Create ./agents/<name>/agent.yaml"))
    else:
        current = ctx.active_agent.name if ctx.active_agent else None
        for a in agents:
            marker = c(C.GREEN, "●") if a.name == current else " "
            desc = a.description or c(C.GRAY, "(no description)")
            print(f"  {marker} {c(C.CYAN, a.name):30s} {desc}")
    return DispatchResult.CONTINUE


def _handle_agent(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    if len(parts) < 2:
        name = ctx.active_agent.name if ctx.active_agent else "(none)"
        print(f"  {c(C.GRAY, 'Active agent:')} {name}")
        print(f"  {c(C.GRAY, 'Usage: /agent <name>  (or /agent none to clear)')}")
        return DispatchResult.CONTINUE

    target = parts[1]
    if target in ("none", "clear", "off"):
        ctx.active_agent = None
    else:
        picked = get_agent(target)
        if picked is None:
            print(c(C.RED, f"  Agent not found: {target}"))
            return DispatchResult.CONTINUE
        ctx.active_agent = picked

    # Re-apply scope
    if ctx.active_agent and ctx.active_agent.provider:
        ctx.provider = ctx.active_agent.provider
    if ctx.active_agent and ctx.active_agent.temperature is not None:
        ctx.temperature = ctx.active_agent.temperature
    ctx.cfg = get_provider_config(ctx.provider)
    if ctx.active_agent and ctx.active_agent.model:
        ctx.cfg["model"] = ctx.active_agent.model
    ctx.system_prompt = build_system_prompt(ctx.active_agent)
    ctx.get_tool_fn, ctx.tools = get_tools_for_agent(ctx.active_agent)
    ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
    ctx.history.clear()
    ctx.session_id = generate_session_id()
    name = ctx.active_agent.name if ctx.active_agent else "(none)"
    print(
        f"  {c(C.GREEN, '✓')} Switched to agent: {name} "
        f"({len(ctx.tools)} tools, provider={ctx.provider}, model={ctx.cfg['model']})"
    )
    return DispatchResult.CONTINUE


def _handle_model(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    providers_list = get_available_providers()

    target = None
    if len(parts) >= 2:
        target = parts[1]
    else:
        print(f"  {c(C.GRAY, 'Current:')} {c(C.CYAN, ctx.provider)} → {ctx.cfg['model']}")
        print(f"  {c(C.GRAY, 'Available:')}")
        for p in providers_list:
            mark = "●" if p["id"] == ctx.provider else " "
            color = C.GREEN if p["available"] else C.GRAY
            avail = "" if p["available"] else c(C.GRAY, " (no key)")
            print(f"    {c(color, mark)} {c(C.CYAN, p['id']):15} → {p['model']}{avail}")
        print(f"  {c(C.GRAY, 'Usage: /model <provider>')}")
        return DispatchResult.CONTINUE

    pick = next((p for p in providers_list if p["id"] == target), None)
    if pick is None:
        print(c(C.RED, f"  Unknown provider: {target}"))
        return DispatchResult.CONTINUE
    if not pick["available"]:
        print(c(C.RED, f"  {target} not available — set the API key first."))
        return DispatchResult.CONTINUE

    try:
        new_cfg = get_provider_config(target)
    except RuntimeError as e:
        print(c(C.RED, f"  Error: {e}"))
        return DispatchResult.CONTINUE

    ctx.provider = target
    ctx.cfg = new_cfg
    # Apply active_agent model override (e.g. named agent profiles)
    if ctx.active_agent and ctx.active_agent.model:
        ctx.cfg["model"] = ctx.active_agent.model
    # Rebuild system prompt for new provider and reset conversation state.
    # Carrying over messages from a different provider confuses smaller models.
    ctx.system_prompt = build_system_prompt(ctx.active_agent)
    ctx.messages[:] = [{"role": "system", "content": ctx.system_prompt}]
    ctx.history.clear()
    ctx.session_id = generate_session_id()
    print(
        f"  {c(C.GREEN, '✓')} Switched to {c(C.CYAN, ctx.provider)} → {ctx.cfg['model']}"
    )
    if not ctx.cfg["supports_tools"]:
        print(
            f"  {c(C.YELLOW, '⚠')} {c(C.GRAY, 'chat-only mode — tools disabled for this model')}"
        )
    return DispatchResult.CONTINUE


def _handle_init(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    """Synthesize a prompt asking the agent to draft an ALPHA.md and FALL THROUGH."""
    from alpha.project_context import CONTEXT_FILENAME

    target = Path(os.getcwd()) / CONTEXT_FILENAME
    force = "--force" in parts[1:]
    if target.exists() and not force:
        print_error(
            f"{CONTEXT_FILENAME} already exists at {target}. "
            f"Pass /init --force to overwrite, or delete it first."
        )
        return DispatchResult.CONTINUE

    action = "Overwrite the existing" if target.exists() else "Create a new"
    ctx.user_input_override = (
        f"[/init invoked]\n"
        f"{action} `{CONTEXT_FILENAME}` at {target} that captures "
        f"this project's stable context for Alpha. Steps:\n"
        f"1. Run project_overview to learn the project layout, type, and git status.\n"
        f"2. Read the key manifest(s): pyproject.toml / package.json / "
        f"Cargo.toml / pom.xml / Gemfile / go.mod / requirements.txt — whichever exist.\n"
        f"3. Read README.md if present, plus 1–2 source entry points (main.py, "
        f"src/index.ts, app/main.py, etc.) to confirm the actual stack.\n"
        f"4. Write {CONTEXT_FILENAME} with these sections, in this order:\n"
        f"   - `# {CONTEXT_FILENAME} — <project name>` (one-line title)\n"
        f"   - `## What this project is` — one short paragraph.\n"
        f"   - `## Stack & dependencies` — language version, key libs, package manager.\n"
        f"   - `## How to run / build / test` — exact commands the user types.\n"
        f"   - `## House rules` — conventions you can infer (test framework, "
        f"linter, type-hint policy, comment policy). Mark inferences explicitly.\n"
        f"   - `## Status & docs` — point to STATUS.md / docs/ if they exist.\n"
        f"   - `## Out-of-scope` — anything obviously off-limits "
        f"(e.g. don't edit prompts/, never commit secrets).\n"
        f"5. Keep the file under 4 KB. No filler. No emoji. "
        f"Use plain Markdown. Do not invent commands you have not verified.\n"
        f"6. After writing, print a one-line confirmation summarizing what you "
        f"included and remind the user to review before committing."
    )
    print(
        f"  {c(C.GREEN, '✦')} {c(C.CYAN, '/init')} "
        f"{c(C.GRAY, f'— drafting {CONTEXT_FILENAME} for {os.path.basename(os.getcwd())}')}"
    )
    return DispatchResult.FALL_THROUGH


def _handle_context(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    """Show current context window usage."""
    from alpha.context import (
        COMPRESSION_THRESHOLD,
        MAX_MESSAGES,
        estimate_messages_tokens,
        get_context_limit,
    )

    used = estimate_messages_tokens(ctx.messages)
    limit = get_context_limit(ctx.provider)
    pct = (used / limit * 100) if limit else 0.0
    trigger_at = int(limit * COMPRESSION_THRESHOLD)

    if pct >= 90:
        bar_color = C.RED + C.BOLD
    elif pct >= 70:
        bar_color = C.YELLOW + C.BOLD
    elif pct >= 50:
        bar_color = C.YELLOW
    else:
        bar_color = C.GREEN

    bar_len = 30
    filled = min(bar_len, int(pct / 100 * bar_len))
    if pct > 0 and filled == 0:
        filled = 1
    bar = c(bar_color, "█" * filled) + c(C.GRAY_DARK, "░" * (bar_len - filled))

    print(f"  {c(C.CYAN, 'Provider:')}    {ctx.provider} ({ctx.cfg.get('model', '?')})")
    print(f"  {c(C.CYAN, 'Tokens:')}      {used:,} / {limit:,} ({pct:.1f}%)")
    print(f"  {c(C.CYAN, 'Usage:')}       {bar}")
    print(f"  {c(C.CYAN, 'Messages:')}    {len(ctx.messages)} / {MAX_MESSAGES}")
    print(
        f"  {c(C.CYAN, 'Compresses:')}  at {int(COMPRESSION_THRESHOLD * 100)}% "
        f"({trigger_at:,} tokens) or {MAX_MESSAGES} messages"
    )
    return DispatchResult.CONTINUE


def _handle_help(ctx: ReplContext, parts: list[str]) -> DispatchResult:
    print(f"  {c(C.CYAN, '/init')}     — Draft an ALPHA.md for this project")
    print(f"  {c(C.CYAN, '/clear')}    — Clear history and screen")
    print(f"  {c(C.CYAN, '/history')}  — Show conversation history")
    print(f"  {c(C.CYAN, '/save')}     — Save current session")
    print(f"  {c(C.CYAN, '/load')}     — Load a previous session")
    print(f"  {c(C.CYAN, '/continue')} — Resume from last session")
    print(f"  {c(C.CYAN, '/sessions')} — List saved sessions")
    print(f"  {c(C.CYAN, '/context')}  — Show context window usage")
    print(f"  {c(C.CYAN, '/tools')}    — List available tools")
    print(f"  {c(C.CYAN, '/skills')}   — List registered skills (ready vs inactive)")
    print(f"  {c(C.CYAN, '/mcp')}      — List connected MCP servers")
    print(f"  {c(C.CYAN, '/image')}    — Attach an image (Ctrl+V or Alt+V also works)")
    print(f"  {c(C.CYAN, '/agents')}   — List named agents")
    print(f"  {c(C.CYAN, '/agent')}    — Show/switch active agent")
    print(f"  {c(C.CYAN, '/model')}    — Show/switch provider & model")
    print(f"  {c(C.CYAN, '/<skill>')}  — Invoke a skill by name (e.g. /skill-creator)")
    print(f"  {c(C.CYAN, '/exit')}     — Exit")
    return DispatchResult.CONTINUE


# ─── Dispatch table ─────────────────────────────────────────────────


_DISPATCH: dict[str, Callable[[ReplContext, list[str]], DispatchResult]] = {
    "/exit": _handle_exit,
    "/quit": _handle_exit,
    "/q": _handle_exit,
    "/clear": _handle_clear,
    "/history": _handle_history,
    "/save": _handle_save,
    "/load": _handle_load,
    "/continue": _handle_continue,
    "/sessions": _handle_sessions,
    "/tools": _handle_tools,
    "/skills": _handle_skills,
    "/mcp": _handle_mcp,
    "/agents": _handle_agents,
    "/agent": _handle_agent,
    "/model": _handle_model,
    "/init": _handle_init,
    "/context": _handle_context,
    "/help": _handle_help,
}


# ─── /image — needs the original full input, handled here separately ────


def handle_image(
    ctx: ReplContext, user_input: str, parts: list[str]
) -> DispatchResult:
    """Resolve `/image <path> [message]` and FALL THROUGH with attachment.

    Kept out of ``_DISPATCH`` because it needs the full ``user_input`` to
    extract the optional message after the path (the dispatcher only
    passes ``parts``).
    """
    if len(parts) < 2:
        print(f"  {c(C.GRAY, 'Usage: /image <path> [optional message]')}")
        print(f"  {c(C.GRAY, 'Example: /image /tmp/screenshot.png what is wrong?')}")
        return DispatchResult.CONTINUE

    img_path_str = parts[1]
    img_path = Path(os.path.expanduser(img_path_str))
    if not img_path.is_file():
        print_error(f"Image not found: {img_path}")
        return DispatchResult.CONTINUE

    rest = user_input.split(maxsplit=2)
    msg_text = rest[2] if len(rest) >= 3 else "What's in this image?"
    print(c(C.GRAY, f"  (1 image attached: {img_path.name})"))

    ctx.user_input_override = msg_text
    ctx.image_paths_override = [img_path]
    ctx.history_record_override = f"[image: {img_path.name}] {msg_text}"
    return DispatchResult.FALL_THROUGH


# ─── Skill name resolution (Claude-Code-style /<name>) ──────────────


def _try_skill_dispatch(
    ctx: ReplContext, cmd: str, parts: list[str], user_input: str
) -> DispatchResult:
    skill_name = cmd[1:]
    skill = get_skill(skill_name)

    if skill is None:
        suggestion = get_close_matches(
            skill_name, [s.name for s in list_skills()], n=1
        )
        hint = f" Did you mean /{suggestion[0]}?" if suggestion else ""
        print(c(C.GRAY, f"  Unknown command: {cmd}.{hint}"))
        return DispatchResult.CONTINUE

    skill_args = user_input.split(maxsplit=1)[1] if len(parts) > 1 else ""
    missing = [b for b in skill.requires_bins if not shutil.which(b)]
    if missing:
        print(
            f"  {c(C.YELLOW, '⚠')} Skill '{skill.name}' requires "
            f"bins not on PATH: {', '.join(missing)}"
        )

    ctx.user_input_override = (
        f"[Skill invoked via /{skill.name}]\n"
        "--- BEGIN SKILL INSTRUCTIONS ---\n"
        f"{skill.body}\n"
        "--- END SKILL INSTRUCTIONS ---\n\n"
        f"User input: {skill_args or '(no additional args)'}\n"
        "Follow the skill's instructions above to handle this."
    )
    print(
        f"  {c(C.GREEN, '✦')} Loaded skill: "
        f"{c(C.CYAN, skill.name)} "
        f"{c(C.GRAY, f'({len(skill.body)} chars)')}"
    )
    return DispatchResult.FALL_THROUGH


# ─── Top-level dispatch ─────────────────────────────────────────────


def dispatch(ctx: ReplContext, user_input: str) -> DispatchResult:
    """Entry point called by the REPL loop on slash-command input.

    The caller is expected to have verified that the line starts with
    ``/`` and that the first token has no embedded slash (paths like
    ``/home/user/file`` should NOT reach this function — they're normal
    input).
    """
    parts = user_input.split()
    cmd = parts[0].lower()

    if cmd == "/image":
        return handle_image(ctx, user_input, parts)

    handler = _DISPATCH.get(cmd)
    if handler is not None:
        return handler(ctx, parts)

    return _try_skill_dispatch(ctx, cmd, parts, user_input)
