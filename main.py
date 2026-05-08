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
import logging
import os
import shutil
import sys
import textwrap
from pathlib import Path

from alpha.agents import AgentScope, get_agent, list_agents, load_all_agents
from alpha.attachments import build_user_content
from alpha.config import (
    DEFAULT_PROVIDER,
    get_available_providers,
    get_provider_config,
    load_system_prompt,
)
from alpha import hooks
from alpha.repl_input import cleanup_temp_images, read_input
from alpha.mcp import (
    list_active_servers as list_mcp_servers,
    load_mcp_servers,
    shutdown_mcp_servers,
)
from alpha.skills import get_skill, inject_skill_index, list_skills, load_all_skills
from alpha.display import (
    C,
    ThinkingIndicator,
    c,
    print_approval_request,
    print_banner,
    print_context_compressed,
    print_error,
    print_phase,
    print_providers_list,
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


def _build_system_prompt(agent: AgentScope | None = None) -> str:
    """Load base system prompt, apply agent extras, inject (filtered) skill index."""
    load_all_skills()
    base = load_system_prompt()
    if agent is not None and agent.system_prompt_extra:
        base = f"{base}\n\n# AGENT PROFILE: {agent.name}\n{agent.system_prompt_extra}"
    skill_filter = (
        agent.filter_skills
        if agent is not None and (agent.skills_allow or agent.skills_deny)
        else None
    )
    return inject_skill_index(base, name_filter=skill_filter)


def _get_tools_for_agent(agent: AgentScope | None):
    """Return (get_tool_fn, openai_tools_list) filtered by the agent's tool scope."""
    try:
        from alpha.tools import get_openai_tools, get_tool, load_all_tools

        load_all_tools()
        # MCP tools register into the same registry; load them after the
        # built-in tools so a misbehaving MCP server can't shadow native ones.
        try:
            load_mcp_servers()
        except Exception as e:
            logging.getLogger(__name__).warning("MCP load failed: %s", e)
        if agent is not None and (agent.tools_allow or agent.tools_deny):
            tools = get_openai_tools(name_filter=agent.filter_tools)
        else:
            tools = get_openai_tools()
        return get_tool, tools
    except ImportError:
        return None, []


def _pick_provider_interactive(default: str) -> str:
    """Prompt user to pick a provider at startup. Falls back to `default` on Enter/EOF."""
    providers = get_available_providers()
    print(c(C.CYAN + C.BOLD, "\nSelect a model / provider:"))
    print_providers_list(providers, default=default, numbered=True)

    while True:
        try:
            choice = input(c(C.GRAY, f"\n  Choice [1-{len(providers)}, Enter={default}]: ")).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return default
        if not choice:
            return default

        pick = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                pick = providers[idx]
        else:
            pick = next((p for p in providers if p["id"] == choice), None)

        if pick is None:
            print(c(C.RED, "  Invalid choice."))
            continue
        if not pick["available"]:
            print(c(C.RED, f"  {pick['id']} not available — pick another."))
            continue
        return pick["id"]


def _resolve_active_agent() -> AgentScope | None:
    """Pick the active agent from ALPHA_AGENT env, else a 'default' profile if it exists."""
    load_all_agents()
    explicit = os.getenv("ALPHA_AGENT", "").strip()
    if explicit:
        agent = get_agent(explicit)
        if agent is None:
            print_error(f"Agent '{explicit}' not found (ALPHA_AGENT). Using no profile.")
        return agent
    return get_agent("default")


def _approval_callback(tool_name: str, args: dict) -> bool:
    """Synchronous approval callback for the REPL."""
    return print_approval_request(tool_name, args)


def _shutdown_browser_session():
    """atexit hook: close any persistent browser session.

    `asyncio.run` falha com RuntimeError se ja existe um loop rodando
    (raro em atexit, mas acontece em testes / embedding). Quando isso
    ocorre, usamos um loop dedicado para o shutdown e logamos qualquer
    erro real ao inves de engolir tudo (#055).
    """
    try:
        from alpha.tools.browser_session import shutdown_browser

        try:
            asyncio.get_running_loop()
            # Loop ativo (raro em atexit) — cria um separado para nao
            # interferir. Se nao existir browser aberto, e no-op rapido.
            new_loop = asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(shutdown_browser())
            finally:
                new_loop.close()
        except RuntimeError:
            # Sem loop ativo (caminho normal em atexit).
            asyncio.run(shutdown_browser())
    except ImportError:
        pass  # Playwright nao instalado, sem session pra fechar
    except Exception as e:
        # Logar para diagnostico — antes era engolido em `except: pass`.
        # Usar print ao inves de logger porque atexit roda apos shutdown
        # do logging em alguns paths.
        try:
            print(f"shutdown_browser_session: {type(e).__name__}: {e}",
                  file=sys.stderr)
        except Exception:
            pass


def _shutdown_mcp_servers():
    """atexit hook: terminate any spawned MCP server subprocesses."""
    try:
        shutdown_mcp_servers()
    except Exception:
        pass


def _fire_on_stop():
    """atexit hook: fire user-defined on_stop hooks."""
    try:
        hooks.fire("on_stop", workspace=os.getcwd())
    except Exception:
        pass


atexit.register(_fire_on_stop)
atexit.register(_shutdown_browser_session)
atexit.register(_shutdown_mcp_servers)
atexit.register(cleanup_temp_images)


def _install_sigterm_handler():
    """Trigger atexit cleanup on SIGTERM (#067).

    Sem isto, `kill <pid>` (default SIGTERM, ex: container shutdown,
    systemd timeout) mata o processo SEM rodar atexit hooks: browser
    runtime fica zumbi, sessao nao salva, MCP servers ficam orfaos.
    `signal.signal(SIGTERM, ...)` faz o handler sair via `sys.exit`,
    o que dispara os atexit. Em SO sem SIGTERM (Windows nativo) e
    no-op silencioso.
    """
    import signal as _signal
    if not hasattr(_signal, "SIGTERM"):
        return

    def _on_sigterm(signum, frame):
        try:
            print("\n[ALPHA] SIGTERM received — running cleanup", file=sys.stderr)
        except Exception:
            pass
        sys.exit(143)  # 128 + SIGTERM(15) — convencao POSIX

    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        # Em threads non-main signal.signal levanta — ok ignorar.
        pass


_install_sigterm_handler()


async def _run_once(messages, user_message, provider, temperature, get_tool_fn, tools, workspace=None):
    """Run a single agent turn and display events."""
    from alpha.agent import run_agent

    full_reply = ""
    indicator = ThinkingIndicator("Pensando")
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
                indicator.start("Pensando")

            elif event_type == "approval_needed":
                indicator.stop()

            elif event_type == "context_compressed":
                indicator.stop()
                print_context_compressed(event.get("before", 0), event.get("after", 0))
                indicator.start("Pensando")

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

        # Commands — but only treat as a slash command if the first token is
        # `/word` (no embedded slashes). Paths like `/home/...` fall through
        # to normal input.
        first_token = user_input.split(maxsplit=1)[0]
        if user_input.startswith("/") and "/" not in first_token[1:]:
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
                        # Por default gera novo session_id em vez de reusar
                        # `parts[1]` — antes, edicoes apos `/load` sobrescreviam
                        # silenciosamente a sessao original (#DL018).
                        # `--inplace` mantem o id antigo para quem quer
                        # explicitamente continuar a mesma sessao.
                        if len(parts) >= 3 and parts[2] == "--inplace":
                            session_id = parts[1]
                            print(f"  {c(C.GREEN, f'Loaded {len(loaded)} messages from {parts[1]} (in-place: saves overwrite)')}")
                        else:
                            session_id = generate_session_id()
                            print(f"  {c(C.GREEN, f'Loaded {len(loaded)} messages from {parts[1]} into new session {session_id}')}")
                            print(f"  {c(C.GRAY, '  (use /load <id> --inplace to overwrite the original instead)')}")
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
            elif cmd == "/skills":
                skills = sorted(list_skills(), key=lambda s: s.name)
                if not skills:
                    print(c(C.GRAY, "  No skills registered."))
                    continue
                # Group by availability so the user sees what's invokable now
                # vs. what needs a binary install.
                ready: list = []
                inactive: list = []
                for s in skills:
                    missing = [b for b in s.requires_bins if not shutil.which(b)]
                    (inactive if missing else ready).append((s, missing))
                print(f"  {c(C.GRAY, f'{len(skills)} skills registered '
                              f'({len(ready)} ready, {len(inactive)} inactive)')}")
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
                continue
            elif cmd == "/image":
                if len(parts) < 2:
                    print(f"  {c(C.GRAY, 'Usage: /image <path> [optional message]')}")
                    print(f"  {c(C.GRAY, 'Example: /image /tmp/screenshot.png what is wrong?')}")
                    continue
                img_path_str = parts[1]
                img_path = Path(os.path.expanduser(img_path_str))
                if not img_path.is_file():
                    print_error(f"Image not found: {img_path}")
                    continue
                # The text after the path becomes the user message; default if absent.
                rest = user_input.split(maxsplit=2)
                msg_text = rest[2] if len(rest) >= 3 else "What's in this image?"
                cwd = os.getcwd()
                user_content = build_user_content(
                    f"[CWD: {cwd}]\n{msg_text}", [img_path]
                )
                print(c(C.GRAY, f"  (1 image attached: {img_path.name})"))
                messages.append({"role": "user", "content": user_content})
                history.append({"role": "user", "content": f"[image: {img_path.name}] {msg_text}"})
                print()
                try:
                    reply = asyncio.run(
                        _run_once(
                            messages, msg_text, provider, temperature,
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
                continue
            elif cmd == "/mcp":
                servers = list_mcp_servers()
                if not servers:
                    print(c(C.GRAY, "  No MCP servers connected. Configure .alpha/mcp.json"))
                else:
                    for s in servers:
                        tool_names = ", ".join(s["tools"]) or c(C.GRAY, "(no tools)")
                        print(f"  {c(C.CYAN, s['name']):30s} {tool_names}")
                continue
            elif cmd == "/agents":
                agents = list_agents()
                if not agents:
                    print(c(C.GRAY, "  No agents defined. Create ./agents/<name>/agent.yaml"))
                else:
                    current = active_agent.name if active_agent else None
                    for a in agents:
                        marker = c(C.GREEN, "●") if a.name == current else " "
                        desc = a.description or c(C.GRAY, "(no description)")
                        print(f"  {marker} {c(C.CYAN, a.name):30s} {desc}")
                continue
            elif cmd == "/agent":
                if len(parts) < 2:
                    name = active_agent.name if active_agent else "(none)"
                    print(f"  {c(C.GRAY, 'Active agent:')} {name}")
                    print(f"  {c(C.GRAY, 'Usage: /agent <name>  (or /agent none to clear)')}")
                else:
                    target = parts[1]
                    if target in ("none", "clear", "off"):
                        active_agent = None
                    else:
                        picked = get_agent(target)
                        if picked is None:
                            print(c(C.RED, f"  Agent not found: {target}"))
                            continue
                        active_agent = picked

                    # Re-apply scope
                    if active_agent and active_agent.provider:
                        provider = active_agent.provider
                    if active_agent and active_agent.temperature is not None:
                        temperature = active_agent.temperature
                    cfg = get_provider_config(provider)
                    if active_agent and active_agent.model:
                        cfg["model"] = active_agent.model
                    system_prompt = _build_system_prompt(active_agent)
                    get_tool_fn, tools = _get_tools_for_agent(active_agent)
                    messages[:] = [{"role": "system", "content": system_prompt}]
                    history.clear()
                    session_id = generate_session_id()
                    name = active_agent.name if active_agent else "(none)"
                    print(f"  {c(C.GREEN, '✓')} Switched to agent: {name} "
                          f"({len(tools)} tools, provider={provider}, model={cfg['model']})")
                continue
            elif cmd == "/model":
                providers_list = get_available_providers()

                target = None
                if len(parts) >= 2:
                    target = parts[1]
                else:
                    print(f"  {c(C.GRAY, 'Current:')} {c(C.CYAN, provider)} → {cfg['model']}")
                    print(f"  {c(C.GRAY, 'Available:')}")
                    print_providers_list(providers_list, current=provider, numbered=True)
                    try:
                        choice = input(
                            f"\n  {c(C.YELLOW + C.BOLD, f'Choose [1-{len(providers_list)}, Enter=cancel]:')} "
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        continue
                    if not choice:
                        continue
                    target = choice

                pick = None
                if target.isdigit():
                    idx = int(target) - 1
                    if 0 <= idx < len(providers_list):
                        pick = providers_list[idx]
                else:
                    pick = next((p for p in providers_list if p["id"] == target), None)

                if pick is None:
                    print_error(f"Provider not found: {target}")
                    continue
                if not pick["available"]:
                    print_error(f"{pick['id']} is not available — set the API key first")
                    continue

                try:
                    new_cfg = get_provider_config(pick["id"])
                except RuntimeError as e:
                    print_error(str(e))
                    continue

                provider = pick["id"]
                cfg = new_cfg
                if active_agent and active_agent.model:
                    cfg["model"] = active_agent.model
                # Reset conversation state so the new model starts clean.
                # Otherwise prior turns aimed at a different provider can
                # confuse smaller models (e.g. qwen-coder echoing template
                # placeholders).
                system_prompt = _build_system_prompt(active_agent)
                messages[:] = [{"role": "system", "content": system_prompt}]
                history.clear()
                session_id = generate_session_id()
                print(f"  {c(C.GREEN, '✓')} Switched to {c(C.CYAN, provider)} → {cfg['model']}")
                if not cfg["supports_tools"]:
                    print(f"  {c(C.YELLOW, '⚠')} {c(C.GRAY, 'chat-only mode — tools disabled for this model')}")
                continue
            elif cmd == "/help":
                print(f"  {c(C.CYAN, '/clear')}    — Clear history and screen")
                print(f"  {c(C.CYAN, '/history')}  — Show conversation history")
                print(f"  {c(C.CYAN, '/save')}     — Save current session")
                print(f"  {c(C.CYAN, '/load')}     — Load a previous session")
                print(f"  {c(C.CYAN, '/continue')} — Resume from last session")
                print(f"  {c(C.CYAN, '/sessions')} — List saved sessions")
                print(f"  {c(C.CYAN, '/tools')}    — List available tools")
                print(f"  {c(C.CYAN, '/skills')}   — List registered skills (ready vs inactive)")
                print(f"  {c(C.CYAN, '/mcp')}      — List connected MCP servers")
                print(f"  {c(C.CYAN, '/image')}    — Attach an image (Ctrl+V or Alt+V also works)")
                print(f"  {c(C.CYAN, '/agents')}   — List named agents")
                print(f"  {c(C.CYAN, '/agent')}    — Show/switch active agent")
                print(f"  {c(C.CYAN, '/model')}    — Show/switch provider & model")
                print(f"  {c(C.CYAN, '/<skill>')}  — Invoke a skill by name (e.g. /skill-creator)")
                print(f"  {c(C.CYAN, '/exit')}     — Exit")
                continue
            else:
                # Try resolving as a skill name (Claude-Code-style /<name>).
                # If a skill matches, inline its body as a synthetic user
                # prompt and fall through to the LLM call. Otherwise show
                # an "unknown command" hint with the closest skill name.
                skill_name = cmd[1:]
                skill = get_skill(skill_name)
                if skill is None:
                    from difflib import get_close_matches
                    suggestion = get_close_matches(
                        skill_name, [s.name for s in list_skills()], n=1
                    )
                    hint = f" Did you mean /{suggestion[0]}?" if suggestion else ""
                    print(c(C.GRAY, f"  Unknown command: {cmd}.{hint}"))
                    continue

                skill_args = (
                    user_input.split(maxsplit=1)[1] if len(parts) > 1 else ""
                )
                missing = [b for b in skill.requires_bins if not shutil.which(b)]
                if missing:
                    print(
                        f"  {c(C.YELLOW, '⚠')} Skill '{skill.name}' requires "
                        f"bins not on PATH: {', '.join(missing)}"
                    )
                user_input = (
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
                # Fall through (no continue) — LLM call handles user_input below.

        # Inject CWD context
        cwd = os.getcwd()
        contextualized = f"[CWD: {cwd}]\n{user_input}"
        user_content = build_user_content(contextualized, image_paths)
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
        history.append({"role": "user", "content": user_input})

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
