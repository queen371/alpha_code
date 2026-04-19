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
import atexit
import json
import os
import sys
import textwrap

from alpha.config import DEFAULT_PROVIDER, get_provider_config, load_system_prompt
from alpha.display import (
    C,
    c,
    print_approval_request,
    print_banner,
    print_context_compressed,
    print_error,
    print_phase,
    print_sessions_list,
    print_tool_call,
    print_tool_result,
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


def _get_tools():
    """Load tools and return (get_tool_fn, openai_tools_list)."""
    try:
        from alpha.tools import get_openai_tools, get_tool, load_all_tools

        load_all_tools()
        tools = get_openai_tools()
        return get_tool, tools
    except ImportError:
        # Tools module not available — agent will run without tools
        return None, []


def _approval_callback(tool_name: str, args: dict) -> bool:
    """Synchronous approval callback for the REPL."""
    return print_approval_request(tool_name, args)


def _shutdown_browser_session():
    """atexit hook: close any persistent browser session."""
    try:
        from alpha.tools.browser_session import shutdown_browser

        asyncio.run(shutdown_browser())
    except Exception:
        pass


atexit.register(_shutdown_browser_session)


async def _run_once(messages, user_message, provider, temperature, get_tool_fn, tools):
    """Run a single agent turn and display events."""
    from alpha.agent import run_agent

    full_reply = ""

    async for event in run_agent(
        messages,
        user_message,
        temperature=temperature,
        provider=provider,
        get_tool_fn=get_tool_fn,
        tools=tools,
        approval_callback=_approval_callback,
    ):
        event_type = event.get("type", "")

        if event_type == "token":
            text = event.get("text", "")
            sys.stdout.write(text)
            sys.stdout.flush()
            full_reply += text

        elif event_type == "tool_call":
            print_tool_call(event["name"], event.get("args", {}), event.get("safety", "safe"))

        elif event_type == "tool_result":
            print_tool_result(event["name"], event.get("result", {}))

        elif event_type == "approval_needed":
            # Approval is handled inside executor via callback
            pass

        elif event_type == "context_compressed":
            print_context_compressed(event.get("before", 0), event.get("after", 0))

        elif event_type == "done":
            reply = event.get("reply", "")
            if reply and not full_reply:
                full_reply = reply

        elif event_type == "error":
            print_error(event.get("message", "Unknown error"))

    # Ensure newline after streaming
    if full_reply and not full_reply.endswith("\n"):
        print()

    return full_reply


def run_repl(provider: str, temperature: float):
    """Interactive REPL loop."""
    cfg = get_provider_config(provider)
    print_banner(provider, cfg["model"])

    system_prompt = load_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    history = []
    session_id = generate_session_id()

    get_tool_fn, tools = _get_tools()

    if tools:
        print_phase(f"Loaded {len(tools)} tools")
    else:
        print_phase("No tools loaded — running in chat-only mode")

    while True:
        try:
            prompt = f"{c(C.GREEN + C.BOLD, '❯')} "
            user_input = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            # Auto-save on exit
            if len(messages) > 1:
                save_session(session_id, messages, {"provider": provider, "model": cfg["model"]})
                print(f"\n  {c(C.GRAY, f'Session saved: {session_id}')}")
            print(f"{c(C.GRAY, 'Goodbye.')}")
            break

        if not user_input:
            continue

        # Commands
        if user_input.startswith("/"):
            parts = user_input.split()
            cmd = parts[0].lower()
            if cmd in ("/exit", "/quit", "/q"):
                if len(messages) > 1:
                    save_session(session_id, messages, {"provider": provider, "model": cfg["model"]})
                    print(f"  {c(C.GRAY, f'Session saved: {session_id}')}")
                print(c(C.GRAY, "Goodbye."))
                break
            elif cmd == "/clear":
                history.clear()
                messages[:] = [{"role": "system", "content": system_prompt}]
                session_id = generate_session_id()
                reset_approve_all()
                os.system("clear" if os.name != "nt" else "cls")
                print_banner(provider, cfg["model"])
                continue
            elif cmd == "/history":
                if not history:
                    print(c(C.GRAY, "  History is empty."))
                else:
                    for msg in history[-20:]:
                        role = msg["role"]
                        content = msg["content"][:100]
                        color = C.GREEN if role == "user" else C.CYAN
                        print(f"  {c(color, role)}: {content}")
                continue
            elif cmd == "/save":
                save_session(session_id, messages, {"provider": provider, "model": cfg["model"]})
                print(f"  {c(C.GREEN, f'Session saved: {session_id}')}")
                continue
            elif cmd == "/load":
                if len(parts) < 2:
                    # Show available sessions
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
                else:
                    loaded = load_session(parts[1])
                    if loaded is None:
                        print(c(C.RED, f"  Session not found: {parts[1]}"))
                    else:
                        messages[:] = [{"role": "system", "content": system_prompt}]
                        messages.extend(loaded)
                        history.clear()
                        history.extend(m for m in loaded if m["role"] in ("user", "assistant"))
                        session_id = parts[1]
                        print(f"  {c(C.GREEN, f'Loaded {len(loaded)} messages from {parts[1]}')}")
                continue
            elif cmd == "/continue":
                # Resume from last session using its summary as context
                last_id = get_last_session_id()
                if not last_id:
                    print(c(C.GRAY, "  No previous session found."))
                    continue
                summary = load_session_summary(last_id)
                if not summary:
                    # Fallback: load full session
                    loaded = load_session(last_id)
                    if loaded is None:
                        print(c(C.RED, f"  Failed to load session: {last_id}"))
                        continue
                    messages[:] = [{"role": "system", "content": system_prompt}]
                    messages.extend(loaded)
                    history.clear()
                    history.extend(m for m in loaded if m["role"] in ("user", "assistant"))
                    print(f"  {c(C.GREEN, f'Resumed {len(loaded)} messages from {last_id}')}")
                else:
                    # Inject compressed summary as context
                    messages[:] = [{"role": "system", "content": system_prompt}]
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[CONTEXT FROM PREVIOUS SESSION {last_id}]\n\n"
                            f"{summary}\n\n"
                            "[End of previous context. Continue from here.]"
                        ),
                    })
                    messages.append({
                        "role": "assistant",
                        "content": (
                            "Understood. I have the context from our previous session. "
                            "How would you like to continue?"
                        ),
                    })
                    history.clear()
                    print(f"  {c(C.GREEN, f'Resumed with summary from {last_id}')}")
                session_id = generate_session_id()
                continue
            elif cmd == "/sessions":
                print_sessions_list(list_sessions(20))
                continue
            elif cmd == "/tools":
                print_tools_list(tools)
                continue
            elif cmd == "/help":
                print(f"  {c(C.CYAN, '/clear')}    — Clear history and screen")
                print(f"  {c(C.CYAN, '/history')}  — Show conversation history")
                print(f"  {c(C.CYAN, '/save')}     — Save current session")
                print(f"  {c(C.CYAN, '/load')}     — Load a previous session")
                print(f"  {c(C.CYAN, '/continue')} — Resume from last session")
                print(f"  {c(C.CYAN, '/sessions')} — List saved sessions")
                print(f"  {c(C.CYAN, '/tools')}    — List available tools")
                print(f"  {c(C.CYAN, '/exit')}     — Exit")
                continue
            else:
                print(c(C.GRAY, f"  Unknown command: {cmd}"))
                continue

        # Inject CWD context
        cwd = os.getcwd()
        contextualized = f"[CWD: {cwd}]\n{user_input}"

        messages.append({"role": "user", "content": contextualized})
        history.append({"role": "user", "content": user_input})

        print()
        try:
            reply = asyncio.run(
                _run_once(messages, user_input, provider, temperature, get_tool_fn, tools)
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
    system_prompt = load_system_prompt()
    cwd = os.getcwd()
    contextualized = f"[CWD: {cwd}]\n{message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": contextualized},
    ]

    get_tool_fn, tools = _get_tools()

    try:
        reply = asyncio.run(
            _run_once(messages, message, provider, temperature, get_tool_fn, tools)
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

    args = parser.parse_args()

    if args.list_providers:
        from alpha.config import get_available_providers

        for p in get_available_providers():
            status = c(C.GREEN, "available") if p["available"] else c(C.RED, "no key")
            print(f"  {c(C.CYAN, p['id']):20s} {p['model']:30s} {status}")
        return

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
