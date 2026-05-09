"""
Tool executor for Alpha Code.

Executes tool calls with parallel support, handles approval flow, formats results.
When multiple tools are called in the same turn, independent tools run in parallel.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable

from . import hooks
from .config import (
    SLOW_TOOL_TIMEOUT as _CFG_SLOW_TOOL_TIMEOUT,
    TOOL_EXECUTION_TIMEOUT as _CFG_TOOL_EXECUTION_TIMEOUT,
    TOOL_RESULT_MAX_CHARS,
)

logger = logging.getLogger(__name__)

# #D003 (V1.0): re-export por retro-compat. O codigo legacy (composite_tools,
# tests) importava daqui. Fonte unica de verdade vive em `alpha.config`.
TOOL_EXECUTION_TIMEOUT = _CFG_TOOL_EXECUTION_TIMEOUT
_SLOW_TOOL_TIMEOUT = _CFG_SLOW_TOOL_TIMEOUT
_SLOW_TOOLS = frozenset({
    "investigate_person", "deploy_check", "run_tests",
    "delegate_task", "delegate_parallel",
    "execute_shell", "execute_pipeline",
    "browser_open", "browser_navigate", "browser_get_content",
    "browser_describe_page", "browser_screenshot",
    "browser_click", "browser_fill", "browser_wait_for",
    "browser_new_tab",
})


def _no_deny(_tool: str, _args: dict) -> tuple[bool, str]:
    return False, ""


def build_assistant_tool_message(
    content: str | None,
    tool_calls: list[dict],
    reasoning_content: str | None = None,
) -> dict:
    """Build an OpenAI-compatible assistant message containing tool_calls.

    `reasoning_content` (DeepSeek-reasoner thinking-mode tokens) deve ser
    devolvido na proxima request ou o provider responde HTTP 400. Quando
    nao usado (qualquer outro modelo), o caller passa None e o campo nao
    e adicionado — providers que nao reconhecem o campo simplesmente
    ignoram, mas omitir e mais limpo.
    """
    msg: dict = {
        "role": "assistant",
        "content": content if content else "",
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }
            for tc in tool_calls
        ],
    }
    if reasoning_content:
        msg["reasoning_content"] = reasoning_content
    return msg


_PREVIEW_FIELD_MAX = 1000


def _format_result(result: dict, tool_name: str) -> str:
    """Truncate and format a tool result for inclusion in messages.

    Hot path: a estimativa cheap (sum de len(str(v))) evita o `json.dumps`
    completo da resposta inteira so para descobrir o tamanho. Se passar do
    limite, cada campo de string e cortado por chars (limite por campo) — a
    versao antiga fatiava no meio do JSON ja serializado, podendo cortar em
    `\\uXXXX` ou em multi-byte UTF-8 e produzir saida com texto corrompido.
    """
    # Drop underscore-prefixed keys: convention for UI-only fields (e.g.
    # `_previous_content` used to render the edit diff in the terminal,
    # but pointless and bloating to send back to the LLM).
    result = {k: v for k, v in result.items() if not (isinstance(k, str) and k.startswith("_"))}
    estimated = sum(len(str(v)) for v in result.values()) + 100
    if estimated <= TOOL_RESULT_MAX_CHARS:
        return json.dumps(result, ensure_ascii=False)

    preview = {
        k: (v[:_PREVIEW_FIELD_MAX] if isinstance(v, str) else v)
        for k, v in result.items()
    }
    truncated = {
        "truncated": True,
        "tool": tool_name,
        "preview": preview,
        "message": (
            f"Result truncated (estimated {estimated} chars > "
            f"{TOOL_RESULT_MAX_CHARS}); strings clipped to "
            f"{_PREVIEW_FIELD_MAX} per field."
        ),
    }
    out = json.dumps(truncated, ensure_ascii=False)
    if len(out) > TOOL_RESULT_MAX_CHARS:
        # AUDIT_V1.2 #019: slicing raw JSON mid-string produces invalid JSON
        # (unbalanced braces/quotes), which causes provider HTTP 400.
        # Replace with a minimal valid JSON object.
        minimal = {
            "truncated": True,
            "tool": tool_name,
            "message": (
                f"Result too large even after preview truncation "
                f"(>{TOOL_RESULT_MAX_CHARS} chars). Use offset/limit args "
                f"on this tool or split the work."
            ),
        }
        out = json.dumps(minimal, ensure_ascii=False)
    return out


def _annotate_error(result: dict, category: str) -> dict:
    """Adicionar invariante `{ok: false, category}` em resultados de erro.

    DEEP_LOGIC #DL017: errors apareciam em N formatos diferentes
    (`{"error": ...}`, `{"skipped": True, ...}`, `{"workspace_violation": True, ...}`),
    forcando o modelo a aprender variantes pra detectar falha. Mantemos os
    campos legados pra retrocompatibilidade mas garantimos que toda falha
    tem `ok=false` + `category` em uma das categorias conhecidas:
    `denied`, `timeout`, `violation`, `runtime`, `parse_error`, `unknown_tool`.
    """
    out = dict(result)
    out.setdefault("ok", False)
    out.setdefault("category", category)
    return out


def _append_tool_msg(messages: list[dict], tc_id: str, result: dict, tool_name: str) -> None:
    """Append a tool result message with truncation + UTF-8 safe formatting.

    Centraliza o `messages.append({...content: ...})` para que erro paths
    e success paths usem o mesmo `_format_result` (truncamento, preview por
    campo). Sem este helper, error messages com paths absolutos longos
    podiam passar de TOOL_RESULT_MAX_CHARS (#D022).
    """
    content = _format_result(result, tool_name)
    messages.append({
        "role": "tool",
        "tool_call_id": tc_id,
        "content": f"<tool_result>{content}</tool_result>",
    })


def _record_skip(tc: dict, tool_name: str, result: dict, messages: list[dict]) -> dict:
    """Append a denied/skipped result to messages and return the event dict."""
    annotated = _annotate_error(result, "denied")
    _append_tool_msg(messages, tc["id"], annotated, tool_name)
    return {"type": "tool_result", "name": tool_name, "result": annotated, "denied": True}


def _record_result(tc: dict, tool_name: str, result: dict, messages: list[dict]) -> None:
    # DL033: tools that return {"error": "..."} on their own (git, db, file
    # tools) don't get the {ok: false, category: ...} invariant added by the
    # executor. Annotate here so the model always sees a consistent error shape.
    if isinstance(result, dict) and "error" in result and "ok" not in result:
        result = _annotate_error(result, "tool_error")
    _append_tool_msg(messages, tc["id"], result, tool_name)


async def _execute_single_tool(tool_def, tool_name: str, args: dict) -> dict:
    """Execute a single tool with timeout. Returns result dict."""
    tool_timeout = (
        _SLOW_TOOL_TIMEOUT if tool_name in _SLOW_TOOLS else TOOL_EXECUTION_TIMEOUT
    )
    try:
        result = await asyncio.wait_for(
            tool_def.executor(**args),
            timeout=tool_timeout,
        )
    except TimeoutError:
        logger.error(f"Tool timeout ({tool_name}): {tool_timeout}s")
        result = _annotate_error(
            {"error": f"Execution timed out ({tool_timeout}s)"}, "timeout"
        )
    except Exception as e:
        logger.error(f"Tool error ({tool_name}): {type(e).__name__}: {e}")
        result = _annotate_error(
            {"error": f"{type(e).__name__}: {e}"}, "runtime"
        )
    return result


async def _fire_pre_tool(
    tool_name: str, args: dict, workspace: str | None
) -> hooks.HookOutcome:
    # Fast path: skip the thread-hop entirely when no hooks are configured.
    if not hooks.has_event("pre_tool"):
        return hooks.HookOutcome()
    return await asyncio.to_thread(
        hooks.fire,
        "pre_tool",
        tool_name=tool_name,
        tool_args=args,
        workspace=workspace,
    )


async def _fire_post_tool(
    tool_name: str, args: dict, result: dict, workspace: str | None
) -> None:
    if not hooks.has_event("post_tool"):
        return
    await asyncio.to_thread(
        hooks.fire,
        "post_tool",
        tool_name=tool_name,
        tool_args=args,
        workspace=workspace,
        extra={"tool_result": result},
    )


def _enforce_workspace(
    workspace: str | None, tool_name: str, args: dict
) -> tuple[bool, dict, str]:
    """Apply workspace validation to args. Returns (ok, new_args, error_msg)."""
    if not workspace:
        return True, args, ""
    from .agents import validate_workspace_args

    return validate_workspace_args(workspace, tool_name, args)


def _parse_and_validate_args(
    tc: dict, tool_def
) -> tuple[dict | None, dict | None]:
    """Parse JSON args do tool_call e validar contra o schema declarado.

    Retorna `(args, None)` em sucesso, ou `(None, error_result)` em falha
    — `error_result` ja e o dict pronto para virar `tool` message,
    permitindo o modelo se auto-corrigir em vez de receber TypeError opaco.

    Diferente da versao anterior (`args = {}` silencioso em JSONDecodeError),
    qualquer falha vira feedback estruturado. Argumentos extras (fora do
    schema) sao DESCARTADOS silenciosamente para evitar TypeError no `**args`
    do executor — o modelo se auto-corrige na proxima iteracao se o resultado
    indicar que campo faltou.
    """
    raw = tc.get("arguments", "")
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        return None, {
            "error": "Invalid JSON in tool arguments",
            "detail": str(e),
            "raw_preview": (raw or "")[:200],
        }
    if not isinstance(args, dict):
        return None, {
            "error": "Tool arguments must be a JSON object",
            "got_type": type(args).__name__,
        }

    if tool_def is None:
        return args, None

    schema = getattr(tool_def, "parameters", None) or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    missing = [k for k in required if k not in args]
    if missing:
        return None, {
            "error": "Tool args missing required fields",
            "missing": missing,
            "required": list(required),
        }

    # Filtrar campos fora do schema — chamada com `**args` faria TypeError
    # se o tool nao declara **kwargs no executor.
    if properties:
        args = {k: v for k, v in args.items() if k in properties}

    return args, None


# ── Shared tool-call validation (parallel + sequential) ──
# #D004: extrai o loop de validacao per-tc (~30 linhas) duplicado entre
# `execute_tool_calls` e `_execute_sequential`. Ambas fazem: lookup →
# parse args → enforce workspace → extract safety. Centralizado aqui.

def _validate_tool_call(
    tc: dict,
    get_tool_fn: Callable | None,
    workspace: str | None,
) -> tuple[dict | None, str, dict, str, str, object]:
    """Validate one tool call. Returns (error_result, tool_name, args, safety_str, error_category, tool_def).

    If error_result is not None: validation failed — caller yields error events.
    Otherwise: (None, tool_name, args_with_workspace, safety_str, "", tool_def) — proceed to execute.
    """
    tool_name = tc["name"]
    tool_def = get_tool_fn(tool_name) if get_tool_fn else None

    if tool_def is None:
        return (
            {"error": f"Unknown tool: {tool_name}"},
            tool_name, {}, "unknown", "unknown_tool", None,
        )

    args, parse_error = _parse_and_validate_args(tc, tool_def)
    if parse_error is not None:
        return (parse_error, tool_name, {}, "denied", "parse_error", tool_def)

    ok, new_args, err = _enforce_workspace(workspace, tool_name, args)
    if not ok:
        return (
            {"error": err, "workspace_violation": True},
            tool_name, {}, "denied", "violation", tool_def,
        )

    safety = getattr(tool_def, "safety", None)
    safety_str = safety.value if hasattr(safety, "value") else "safe"
    return (None, tool_name, new_args, safety_str, "", tool_def)


async def execute_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
    workspace: str | None = None,
    is_denied_fn: Callable[[str, dict], tuple[bool, str]] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Execute tool calls, yielding events for each step.

    When multiple tools are called, auto-approved tools run in PARALLEL.
    Approval-requiring tools are handled sequentially first.

    Yields:
        {"type": "tool_call", ...}
        {"type": "approval_needed", ...}
        {"type": "tool_result", ...}
    """
    is_denied_fn = is_denied_fn or _no_deny

    if len(tool_calls) == 1:
        async for event in _execute_sequential(
            tool_calls, messages, needs_approval_fn, approval_callback, get_tool_fn,
            workspace=workspace, is_denied_fn=is_denied_fn,
        ):
            yield event
        return

    prepared = []  # (tc, tool_name, args, tool_def, safety_str)

    for tc in tool_calls:
        error, tool_name, args, safety_str, category, tool_def = _validate_tool_call(
            tc, get_tool_fn, workspace
        )
        if error is not None:
            error = _annotate_error(error, category)
            yield {"type": "tool_call", "name": tool_name, "args": {}, "safety": safety_str}
            yield {"type": "tool_result", "name": tool_name, "result": error}
            _append_tool_msg(messages, tc["id"], error, tool_name)
            continue

        prepared.append((tc, tool_name, args, tool_def, safety_str))

    for tc, tool_name, args, tool_def, safety_str in prepared:
        yield {"type": "tool_call", "name": tool_name, "args": args, "safety": safety_str}

    approved = []
    for tc, tool_name, args, tool_def, _ in prepared:
        denied, reason = is_denied_fn(tool_name, args)
        if denied:
            result = {"skipped": True, "reason": reason, "denied_by_rule": True}
            yield _record_skip(tc, tool_name, result, messages)
            continue

        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}
            user_approved = (
                await asyncio.to_thread(approval_callback, tool_name, args)
                if approval_callback else False
            )
            if not user_approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield _record_skip(tc, tool_name, result, messages)
                continue

        approved.append((tc, tool_name, args, tool_def))

    if not approved:
        return

    # pre_tool hooks fire in parallel — they're independent shell processes.
    outcomes = await asyncio.gather(
        *[_fire_pre_tool(name, args, workspace) for _, name, args, _ in approved]
    )
    runnable = []
    for (tc, tool_name, args, tool_def), outcome in zip(approved, outcomes):
        if outcome.blocked:
            result = {
                "skipped": True,
                "reason": f"Hook blocked: {outcome.block_reason}",
                "hook_blocked": True,
            }
            yield _record_skip(tc, tool_name, result, messages)
            continue
        runnable.append((tc, tool_name, args, tool_def))

    if not runnable:
        return

    async def _run(item):
        tc, tool_name, args, tool_def = item
        result = await _execute_single_tool(tool_def, tool_name, args)
        return tc, tool_name, args, result

    results = await asyncio.gather(*[_run(item) for item in runnable], return_exceptions=True)

    post_tasks = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            tc, tool_name, args, _ = runnable[i]
            logger.error(f"Parallel tool execution error ({tool_name}): {type(r).__name__}: {r}")
            result = {"error": f"{type(r).__name__}: {r}"}
            yield {"type": "tool_result", "name": tool_name, "result": result}
            _append_tool_msg(messages, tc["id"], result, tool_name)
            continue

        tc, tool_name, args, result = r
        post_tasks.append(_fire_post_tool(tool_name, args, result, workspace))
        yield {"type": "tool_result", "name": tool_name, "result": result}
        _record_result(tc, tool_name, result, messages)

    if post_tasks:
        await asyncio.gather(*post_tasks, return_exceptions=True)


async def _execute_sequential(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
    workspace: str | None = None,
    is_denied_fn: Callable[[str, dict], tuple[bool, str]] = _no_deny,
) -> AsyncGenerator[dict, None]:
    for tc in tool_calls:
        error, tool_name, args, safety_str, category, tool_def = _validate_tool_call(
            tc, get_tool_fn, workspace
        )
        if error is not None:
            error = _annotate_error(error, category)
            yield {"type": "tool_call", "name": tool_name, "args": {}, "safety": safety_str}
            yield {"type": "tool_result", "name": tool_name, "result": error}
            _append_tool_msg(messages, tc["id"], error, tool_name)
            continue

        yield {"type": "tool_call", "name": tool_name, "args": args, "safety": safety_str}

        denied, reason = is_denied_fn(tool_name, args)
        if denied:
            result = {"skipped": True, "reason": reason, "denied_by_rule": True}
            yield _record_skip(tc, tool_name, result, messages)
            continue

        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}
            approved = (
                await asyncio.to_thread(approval_callback, tool_name, args)
                if approval_callback else False
            )
            if not approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield _record_skip(tc, tool_name, result, messages)
                continue

        outcome = await _fire_pre_tool(tool_name, args, workspace)
        if outcome.blocked:
            result = {
                "skipped": True,
                "reason": f"Hook blocked: {outcome.block_reason}",
                "hook_blocked": True,
            }
            yield _record_skip(tc, tool_name, result, messages)
            continue

        result = await _execute_single_tool(tool_def, tool_name, args)
        await _fire_post_tool(tool_name, args, result, workspace)
        yield {"type": "tool_result", "name": tool_name, "result": result}
        _record_result(tc, tool_name, result, messages)
