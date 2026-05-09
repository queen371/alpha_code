"""Workspace enforcement — validates tool args against an agent's workspace.

If an AgentScope has a workspace set, any tool that touches the filesystem
must have its path arguments resolved within that workspace. Absolute paths
outside the workspace are rejected; relative paths are rewritten to be
workspace-relative.
"""

from __future__ import annotations

from pathlib import Path

# Map of tool_name -> list of arg names that carry filesystem paths.
# Tools not in this map receive no workspace validation (they either don't
# touch the filesystem or have their own sandboxing).
PATH_PARAMS_BY_TOOL: dict[str, list[str]] = {
    "read_file": ["path"],
    "write_file": ["path"],
    "edit_file": ["path"],
    "list_directory": ["path"],
    "search_files": ["path"],
    "glob_files": ["path"],
    "search_and_replace": ["path"],
    "execute_shell": ["cwd"],
    "execute_pipeline": ["cwd"],
    "git_operation": ["path"],
    "run_tests": ["path"],
    "project_overview": ["path"],
}

# Tools where an empty path should default to the workspace instead of CWD.
DEFAULT_TO_WORKSPACE: set[str] = {
    "execute_shell",
    "execute_pipeline",
    "git_operation",
    "run_tests",
    "project_overview",
}


def validate_args(
    workspace: str,
    tool_name: str,
    args: dict,
) -> tuple[bool, dict, str]:
    """Validate/rewrite tool args against the workspace.

    Returns:
        (ok, new_args, error_msg)
        - ok=True: args validated (possibly rewritten with absolute workspace-relative paths)
        - ok=False: args rejected; error_msg explains why
    """
    params = PATH_PARAMS_BY_TOOL.get(tool_name)
    if not params:
        return True, args, ""

    ws = Path(workspace).expanduser().resolve()
    new_args = dict(args)

    for param in params:
        val = args.get(param)
        if not val:
            if tool_name in DEFAULT_TO_WORKSPACE:
                new_args[param] = str(ws)
            continue

        p = Path(str(val)).expanduser()
        if not p.is_absolute():
            p = ws / p

        try:
            resolved = p.resolve()
        except (OSError, RuntimeError) as e:
            return False, args, f"Cannot resolve path '{val}': {e}"

        try:
            resolved.relative_to(ws)
        except ValueError:
            return False, args, (
                f"Path '{val}' is outside the agent's workspace.\n"
                f"  Workspace: {ws}\n"
                f"  Resolved:  {resolved}\n"
                f"Access denied."
            )

        new_args[param] = str(resolved)

    return True, new_args, ""
