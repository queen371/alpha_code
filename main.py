#!/usr/bin/env python3
"""
Alpha Code — Standalone terminal agent.

Usage:
    python main.py                           # interactive REPL
    python main.py "analyze this project"    # single command
    python main.py --provider grok "fix the bug in main.py"
"""

import argparse
import asyncio
import os
import sys
import textwrap

from alpha.agents import get_agent
from alpha.attachments import build_user_content
from alpha.config import (
    DEFAULT_PROVIDER,
    get_available_providers,
    get_provider_config,
)
from alpha import hooks
from alpha.repl_input import read_input
from alpha.mcp import list_active_servers as list_mcp_servers
from alpha.skills import list_skills
from alpha.display import (
    C,
    ThinkingIndicator,
    c,
    print_banner,
    print_context_compressed,
    print_error,
    print_phase,
    print_providers_list,
    print_tool_call,
    print_tool_result,
)
from alpha.history import generate_session_id, save_session
from alpha.cli.commands import DispatchResult, ReplContext, dispatch
from alpha.cli.lifecycle import install_lifecycle_hooks
from alpha.cli.setup import (
    approval_callback as _approval_callback,
    build_system_prompt as _build_system_prompt,
    get_tools_for_agent as _get_tools_for_agent,
    pick_provider_interactive as _pick_provider_interactive,
    resolve_active_agent as _resolve_active_agent,
)

install_lifecycle_hooks()


async def _run_once(messages, user_message, provider, temperature, get_tool_fn, tools, workspace=None):
    """Run a single agent turn and display events."""
    from alpha.agent import run_agent

    full_reply = ""
    indicator = ThinkingIndicator("Thinking")
    indicator.start()

    try:
        async for event in run_agent(
            messages,
            user_message,
            temperature=temperature,
            provider=provider,
            get_tool_fn=get_tool_fn,
            tools=tools,
            approval_callback=_approval_callback,
            workspace=workspace,
        ):
            event_type = event.get("type", "")

            if event_type == "token":
                indicator.stop()
                text = event.get("text", "")
                sys.stdout.write(text)
                sys.stdout.flush()
                full_reply += text

            elif event_type == "tool_call":
                indicator.stop()
                print_tool_call(event["name"], event.get("args", {}), event.get("safety", "safe"))
                indicator.start(f"Executando {event['name']}")

            elif event_type == "tool_result":
                indicator.stop()
                print_tool_result(event["name"], event.get("result", {}))
                indicator.start("Thinking")

            elif event_type == "approval_needed":
                indicator.stop()

            elif event_type == "context_compressed":
                indicator.stop()
                print_context_compressed(event.get("before", 0), event.get("after", 0))
                indicator.start("Thinking")

            elif event_type == "done":
                indicator.stop()
                reply = event.get("reply", "")
                if reply and not full_reply:
                    full_reply = reply

            elif event_type == "error":
                indicator.stop()
                print_error(event.get("message", "Unknown error"))
    finally:
        indicator.stop()

    # Ensure newline after streaming
    if full_reply and not full_reply.endswith("\n"):
        print()

    return full_reply


def run_repl(provider: str, temperature: float):
    """Interactive REPL loop."""
    active_agent = _resolve_active_agent()
    if active_agent and active_agent.provider:
        provider = active_agent.provider
    if active_agent and active_agent.temperature is not None:
        temperature = active_agent.temperature

    cfg = get_provider_config(provider)
    if active_agent and active_agent.model:
        cfg["model"] = active_agent.model

    print_banner(provider, cfg["model"])

    system_prompt = _build_system_prompt(active_agent)
    messages = [{"role": "system", "content": system_prompt}]
    history = []
    session_id = generate_session_id()

    get_tool_fn, tools = _get_tools_for_agent(active_agent)

    if not cfg["supports_tools"]:
        print_phase(f"{c(C.YELLOW, 'chat-only')} — {provider} does not support tool-calling")
    elif tools:
        print_phase(f"Loaded {len(tools)} tools")
    else:
        print_phase("No tools loaded — running in chat-only mode")

    skills_count = len(list_skills())
    if skills_count:
        print_phase(f"Loaded {skills_count} skills")

    mcp_servers = list_mcp_servers()
    if mcp_servers:
        total_mcp_tools = sum(len(s["tools"]) for s in mcp_servers)
        print_phase(
            f"MCP: {len(mcp_servers)} server(s), {total_mcp_tools} tool(s)"
        )

    if active_agent:
        print_phase(f"Active agent: {active_agent.name}")

    # Surface auto-loaded project context so the user sees what Alpha
    # picked up. Uses the same loader as `_build_system_prompt` — both
    # paths are cheap (single read, capped at MAX_BYTES) so the redundant
    # call is fine.
    from alpha.project_context import load_project_context
    _proj_ctx = load_project_context()
    if _proj_ctx is not None:
        rel = os.path.relpath(_proj_ctx.path)
        size_kb = _proj_ctx.raw_size / 1024
        suffix = " (truncated)" if _proj_ctx.truncated else ""
        print_phase(f"Project context: {rel} ({size_kb:.1f} KB){suffix}")

    while True:
        try:
            prompt = f"{c(C.GREEN + C.BOLD, '❯')} "
            user_input, image_paths = read_input(prompt)
            user_input = user_input.strip()
        except (KeyboardInterrupt, EOFError):
            # Auto-save on exit
            if len(messages) > 1:
                save_session(session_id, messages, {"provider": provider, "model": cfg["model"]})
                print(f"\n  {c(C.GRAY, f'Session saved: {session_id}')}")
            print(f"{c(C.GRAY, 'Goodbye.')}")
            break

        if not user_input and not image_paths:
            continue

        # One-shot agent dispatch: "@name message"
        if user_input.startswith("@"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 2 and len(parts[0]) > 1:
                at_name = parts[0][1:]
                at_msg = parts[1]
                at_agent = get_agent(at_name)
                if at_agent is None:
                    print_error(f"Agent not found: {at_name}")
                    continue

                at_provider = at_agent.provider or provider
                at_temperature = (
                    at_agent.temperature if at_agent.temperature is not None else temperature
                )
                at_system = _build_system_prompt(at_agent)
                at_get_tool, at_tools = _get_tools_for_agent(at_agent)

                print(c(C.GRAY, f"  (one-shot → {at_name})"))
                print()

                cwd = os.getcwd()
                at_messages = [
                    {"role": "system", "content": at_system},
                    {"role": "user", "content": f"[CWD: {cwd}]\n{at_msg}"},
                ]
                try:
                    asyncio.run(
                        _run_once(
                            at_messages, at_msg, at_provider, at_temperature,
                            at_get_tool, at_tools,
                            workspace=at_agent.workspace,
                        )
                    )
                except KeyboardInterrupt:
                    print(c(C.YELLOW, "\n\nInterrupted."))
                print()
                continue

        # Slash command? Dispatch via alpha/cli/commands.
        # Skip when the first token has an embedded `/` so that paths
        # like `/home/foo/bar` fall through to normal input.
        first_token = user_input.split(maxsplit=1)[0]
        if user_input.startswith("/") and "/" not in first_token[1:]:
            ctx = ReplContext(
                messages=messages, history=history, session_id=session_id,
                provider=provider, temperature=temperature, cfg=cfg,
                system_prompt=system_prompt, tools=tools,
                get_tool_fn=get_tool_fn, active_agent=active_agent,
            )
            result = dispatch(ctx, user_input)
            # Pull mutable state back from ctx — handlers may have rebound
            # session_id, provider, cfg, agent, etc.
            messages, history = ctx.messages, ctx.history
            session_id = ctx.session_id
            provider, temperature, cfg = ctx.provider, ctx.temperature, ctx.cfg
            system_prompt, tools, get_tool_fn = ctx.system_prompt, ctx.tools, ctx.get_tool_fn
            active_agent = ctx.active_agent

            if result is DispatchResult.BREAK:
                break
            if result is DispatchResult.CONTINUE:
                continue
            # FALL_THROUGH — handler transformed the input. Use the
            # override values for the LLM call below.
            if ctx.user_input_override is not None:
                user_input = ctx.user_input_override
            if ctx.image_paths_override is not None:
                image_paths = ctx.image_paths_override
            _history_record_override = ctx.history_record_override
        else:
            _history_record_override = None

        # Inject CWD context
        cwd = os.getcwd()
        contextualized = f"[CWD: {cwd}]\n{user_input}"
        if image_paths and not cfg.get("supports_vision", False):
            print_error(
                f"Provider '{provider}' (modelo {cfg['model']}) nao suporta imagens. "
                f"Imagem(s) ignorada(s). Use /provider para trocar para um vision-capable "
                f"(ex: openai, anthropic) ou descreva o conteudo textualmente."
            )
            image_paths = []
        user_content = build_user_content(
            contextualized, image_paths, vision_format=cfg.get("vision_format", "openai")
        )
        if image_paths:
            print(c(C.GRAY, f"  ({len(image_paths)} image(s) attached)"))

        # User-prompt hook (non-blocking; output goes to stderr/log)
        try:
            hooks.fire(
                "on_user_prompt",
                user_prompt=user_input,
                workspace=active_agent.workspace if active_agent else None,
            )
        except Exception:
            pass

        messages.append({"role": "user", "content": user_content})
        # `/image` rewrites the history entry to include the filename so
        # /history doesn't show a giant base64 blob; other commands fall
        # through with override=None and we just record the raw input.
        history.append(
            {"role": "user",
             "content": _history_record_override or user_input}
        )

        print()
        try:
            reply = asyncio.run(
                _run_once(
                    messages, user_input, provider, temperature,
                    get_tool_fn, tools,
                    workspace=active_agent.workspace if active_agent else None,
                )
            )
        except KeyboardInterrupt:
            print(c(C.YELLOW, "\n\nInterrupted."))
            reply = ""
        print()

        if reply:
            messages.append({"role": "assistant", "content": reply})
            history.append({"role": "assistant", "content": reply})


def run_single(provider: str, temperature: float, message: str):
    """Single command mode (non-interactive)."""
    active_agent = _resolve_active_agent()
    if active_agent and active_agent.provider:
        provider = active_agent.provider
    if active_agent and active_agent.temperature is not None:
        temperature = active_agent.temperature

    system_prompt = _build_system_prompt(active_agent)
    cwd = os.getcwd()
    contextualized = f"[CWD: {cwd}]\n{message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": contextualized},
    ]

    get_tool_fn, tools = _get_tools_for_agent(active_agent)

    try:
        reply = asyncio.run(
            _run_once(
                messages, message, provider, temperature,
                get_tool_fn, tools,
                workspace=active_agent.workspace if active_agent else None,
            )
        )
    except KeyboardInterrupt:
        print(c(C.YELLOW, "\nInterrupted."))
        sys.exit(1)

    if not reply:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Alpha Code — Standalone terminal agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python main.py                                    # interactive mode
              python main.py "create a hello world"             # single command
              python main.py --provider grok "analyze this project"
              python main.py --provider ollama "list files"
        """),
    )
    parser.add_argument(
        "message", nargs="?", help="Message to send (interactive mode if omitted)"
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"LLM provider: deepseek, openai, grok, ollama (default: {DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.5,
        help="LLM temperature (default: 0.5)",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List available providers and exit",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Run the onboarding wizard (configure provider, API key, workspace)",
    )
    parser.add_argument(
        "--pick",
        action="store_true",
        help="Interactively choose the provider/model at startup (REPL only)",
    )

    args = parser.parse_args()

    if args.init:
        from alpha.wizard import run_wizard

        sys.exit(0 if run_wizard() else 1)

    if args.list_providers:
        print_providers_list(get_available_providers())
        return

    if args.pick and not args.message:
        args.provider = _pick_provider_interactive(args.provider)

    # Validate provider
    try:
        cfg = get_provider_config(args.provider)
    except RuntimeError as e:
        print_error(str(e))
        sys.exit(1)

    if args.message:
        run_single(args.provider, args.temperature, args.message)
    else:
        run_repl(args.provider, args.temperature)


if __name__ == "__main__":
    main()
