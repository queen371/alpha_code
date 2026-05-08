"""Declarative hook system for Alpha Code.

Hooks let users run shell commands at well-defined points in the agent
lifecycle without modifying the codebase. Configured in `.alpha/settings.json`.

## Schema

```json
{
  "hooks": {
    "pre_tool": [
      {
        "matcher": "write_file|edit_file",
        "command": "echo 'about to write' >> /tmp/audit.log",
        "blocking": true
      }
    ],
    "post_tool": [
      {"matcher": "write_file", "command": "ruff check {path}"}
    ],
    "on_user_prompt": [
      {"command": "echo \"$ALPHA_USER_PROMPT\" >> /tmp/prompts.log"}
    ],
    "on_stop": [
      {"command": "notify-send 'Alpha done'"}
    ]
  }
}
```

## Events

- `pre_tool`  — fires before each tool call. If a `blocking` hook exits
  non-zero, the tool is denied and the LLM sees the hook's stderr as
  feedback.
- `post_tool` — fires after each tool call (regardless of result). Output
  is logged but does not affect execution.
- `on_user_prompt` — fires when the user submits a message in the REPL.
- `on_stop` — fires once per session at shutdown.

## Variables passed to hook commands

The full event JSON is piped to the hook on stdin. The following env vars
are also exported for convenience:

- `ALPHA_HOOK_EVENT` — event name (`pre_tool`, etc.)
- `ALPHA_TOOL_NAME` — tool being called (pre/post_tool only)
- `ALPHA_TOOL_ARGS_JSON` — JSON-encoded args (pre/post_tool only)
- `ALPHA_USER_PROMPT` — user's input text (on_user_prompt only)
- `ALPHA_WORKSPACE` — active workspace path (if any)

## Security note — hooks see all secrets

Unlike tool execution paths (which go through `safe_env` to strip API keys
before subprocess), hooks receive the **raw `os.environ`** including every
credential in `.env` and the parent shell. This is intentional — hooks
need env vars to integrate with linters/CI/audit tools — but it means a
malicious or careless hook command that exfiltrates env (`curl -d "$(env)"`,
`env > /tmp/leak`, etc.) leaks everything.

Treat `.alpha/settings.json` as you would a `.bashrc`: only paste commands
from sources you trust. See `docs/USER_GUIDE.md` § Hooks for the user-facing
warning. Tracked as DEEP_SECURITY V3.0 #D118.

## Matcher

A regex applied to the tool name (pre/post_tool only). If omitted or empty,
the hook fires for every tool. No anchoring — `write` matches both
`write_file` and `rewrite`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .settings import find_config_file, read_json

logger = logging.getLogger(__name__)

VALID_EVENTS = ("pre_tool", "post_tool", "on_user_prompt", "on_stop")
HOOK_TIMEOUT = 30  # seconds


@dataclass
class Hook:
    event: str
    command: str
    matcher: re.Pattern | None = None
    blocking: bool = False


_loaded = False
_hooks: dict[str, list[Hook]] = {}


def _parse_hooks(raw: Any) -> dict[str, list[Hook]]:
    out: dict[str, list[Hook]] = {ev: [] for ev in VALID_EVENTS}
    if not isinstance(raw, dict):
        return out
    for event, entries in raw.items():
        if event not in VALID_EVENTS:
            logger.warning("Unknown hook event '%s' (skipped)", event)
            continue
        if not isinstance(entries, list):
            logger.warning("Hook event '%s' must be a list (skipped)", event)
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            matcher_str = entry.get("matcher") or ""
            matcher = None
            if matcher_str:
                try:
                    matcher = re.compile(matcher_str)
                except re.error as e:
                    logger.warning(
                        "Invalid hook matcher '%s' (%s); ignoring matcher", matcher_str, e
                    )
            out[event].append(
                Hook(
                    event=event,
                    command=command,
                    matcher=matcher,
                    blocking=bool(entry.get("blocking", False)),
                )
            )
    return out


def load_hooks(force: bool = False) -> dict[str, list[Hook]]:
    """Read .alpha/settings.json and parse the hooks block. Cached after first call."""
    global _loaded, _hooks
    if _loaded and not force:
        return _hooks
    raw = read_json(find_config_file("settings.json"), default={})
    hooks_block = raw.get("hooks") if isinstance(raw, dict) else None
    _hooks = _parse_hooks(hooks_block)
    _loaded = True
    return _hooks


def has_event(event: str) -> bool:
    """Cheap synchronous check: are any hooks registered for this event?"""
    return bool(load_hooks().get(event))


def _run_hook(hook: Hook, payload: dict, env_extra: dict[str, str]) -> tuple[int, str, str]:
    """Run a single hook command. Returns (exit_code, stdout, stderr)."""
    env = {**os.environ, **env_extra}
    try:
        proc = subprocess.run(
            hook.command,
            shell=True,
            input=json.dumps(payload, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"hook timed out after {HOOK_TIMEOUT}s"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


@dataclass
class HookOutcome:
    """Aggregated outcome of running all hooks for one event."""
    blocked: bool = False
    block_reason: str = ""
    stderr_lines: list[str] = field(default_factory=list)


def _matches(hook: Hook, tool_name: str | None) -> bool:
    if hook.matcher is None:
        return True
    if tool_name is None:
        return False
    return hook.matcher.search(tool_name) is not None


def fire(
    event: str,
    *,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    user_prompt: str | None = None,
    workspace: str | None = None,
    extra: dict | None = None,
) -> HookOutcome:
    """Fire all hooks registered for `event`. Synchronous (called via to_thread)."""
    hooks = load_hooks().get(event, [])
    if not hooks:
        return HookOutcome()

    payload: dict[str, Any] = {"event": event}
    env_extra: dict[str, str] = {"ALPHA_HOOK_EVENT": event}
    if tool_name is not None:
        payload["tool_name"] = tool_name
        env_extra["ALPHA_TOOL_NAME"] = tool_name
    if tool_args is not None:
        payload["tool_args"] = tool_args
        env_extra["ALPHA_TOOL_ARGS_JSON"] = json.dumps(
            tool_args, ensure_ascii=False, default=str
        )
    if user_prompt is not None:
        payload["user_prompt"] = user_prompt
        env_extra["ALPHA_USER_PROMPT"] = user_prompt
    if workspace:
        payload["workspace"] = workspace
        env_extra["ALPHA_WORKSPACE"] = workspace
    if extra:
        payload.update(extra)

    outcome = HookOutcome()
    for hook in hooks:
        if not _matches(hook, tool_name):
            continue
        exit_code, stdout, stderr = _run_hook(hook, payload, env_extra)
        if stderr.strip():
            outcome.stderr_lines.append(stderr.strip())
            logger.debug("hook[%s] stderr: %s", event, stderr.strip())
        if exit_code != 0 and hook.blocking:
            outcome.blocked = True
            reason = stderr.strip() or stdout.strip() or f"exit {exit_code}"
            outcome.block_reason = reason
            return outcome  # stop on first block
    return outcome


def reset_cache() -> None:
    """Force a re-read on next access. Useful for tests."""
    global _loaded, _hooks
    _loaded = False
    _hooks = {}
