"""Plan-mode and todo-list tools.

Design: stateless. Both tools encode their state into the conversation
itself — the LLM passes the latest plan/todo list every time, and the
display layer renders it. This avoids cross-turn state leakage and keeps
sessions resumable from messages alone.

`present_plan` is marked DESTRUCTIVE so the approval gate fires every time:
the user reviews the plan before any executing tool runs. The tool itself
does nothing — it's the approval prompt that gates execution.

`todo_write` is SAFE (auto-approved) — it's purely informational.
"""

from __future__ import annotations

from typing import Any

from . import ToolDefinition, ToolSafety, register_tool

VALID_TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")


# ── present_plan ──


async def _present_plan(summary: str, steps: list[Any]) -> dict[str, Any]:
    if not isinstance(summary, str) or not summary.strip():
        return {"error": "summary is required"}
    if not isinstance(steps, list) or not steps:
        return {"error": "steps must be a non-empty list"}

    normalized = []
    for i, s in enumerate(steps, start=1):
        text = str(s).strip()
        if not text:
            return {"error": f"step {i} is empty"}
        normalized.append(text)

    return {
        "approved": True,
        "summary": summary.strip(),
        "steps": normalized,
        "message": (
            "Plan approved by user. Proceed with execution. "
            "Do not call present_plan again unless the plan needs to change."
        ),
    }


register_tool(
    ToolDefinition(
        name="present_plan",
        description=(
            "Present a step-by-step execution plan to the user for approval BEFORE "
            "starting any non-trivial work. Call this once at the start of medium "
            "or complex tasks (3+ steps). The user must approve the plan before "
            "you run any modifying tool. After approval, follow the plan; if you "
            "deviate significantly, present_plan again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One-sentence statement of the goal",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of concrete steps you'll take",
                },
            },
            "required": ["summary", "steps"],
        },
        safety=ToolSafety.DESTRUCTIVE,  # forces the approval gate
        executor=_present_plan,
        category="planning",
    )
)


# ── todo_write ──


async def _todo_write(todos: list[Any]) -> dict[str, Any]:
    if not isinstance(todos, list):
        return {"error": "todos must be a list"}

    cleaned = []
    seen_in_progress = 0
    for i, raw in enumerate(todos):
        if not isinstance(raw, dict):
            return {"error": f"todo[{i}] must be an object with 'content' and 'status'"}
        content = str(raw.get("content", "")).strip()
        status = str(raw.get("status", "pending")).strip()
        if not content:
            return {"error": f"todo[{i}] missing 'content'"}
        if status not in VALID_TODO_STATUSES:
            return {
                "error": (
                    f"todo[{i}] has invalid status '{status}'. "
                    f"Must be one of {VALID_TODO_STATUSES}"
                )
            }
        if status == "in_progress":
            seen_in_progress += 1
        cleaned.append({"content": content, "status": status})

    warning = None
    if seen_in_progress > 1:
        warning = (
            f"{seen_in_progress} todos are 'in_progress'. "
            "Prefer keeping exactly one in progress at a time."
        )

    counts = {s: 0 for s in VALID_TODO_STATUSES}
    for t in cleaned:
        counts[t["status"]] += 1

    result: dict[str, Any] = {
        "ok": True,
        "todos": cleaned,
        "counts": counts,
    }
    if warning:
        result["warning"] = warning
    return result


register_tool(
    ToolDefinition(
        name="todo_write",
        description=(
            "Maintain a checklist of subtasks for the current request. Pass the "
            "ENTIRE list every time — this tool replaces, not appends. Use it for "
            "tasks with 3+ distinct steps. Mark exactly one item 'in_progress' "
            "while you're working on it; flip to 'completed' as soon as it's "
            "done. Skip this for trivial single-step requests."
        ),
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Imperative-form description of the subtask",
                            },
                            "status": {
                                "type": "string",
                                "enum": list(VALID_TODO_STATUSES),
                            },
                        },
                        "required": ["content", "status"],
                    },
                    "description": "Full replacement list of all current todos",
                }
            },
            "required": ["todos"],
        },
        safety=ToolSafety.SAFE,
        executor=_todo_write,
        category="planning",
    )
)
