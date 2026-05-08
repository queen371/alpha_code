"""Auto-load per-project context from an ``ALPHA.md`` file.

When the agent starts in a directory that contains ``ALPHA.md`` (or where
any ancestor up to the filesystem root contains one), the file's contents
are appended to the system prompt under a ``# PROJECT CONTEXT`` section.
This lets each project teach Alpha its own conventions, layout, and
gotchas without polluting the global ``prompts/system.md``.

Resolution
----------
The lookup walks **upward** from the CWD, like ``.git`` discovery: the
first ``ALPHA.md`` found wins. This means you can ``cd`` into a
subdirectory and still get the project's context — provided the file
sits at the project root.

Only ``ALPHA.md`` is honored. We deliberately do **not** read
``CLAUDE.md`` or ``AGENTS.md`` (the conventions of other agent tools)
because those files are written for a *different* agent's identity and
can conflict with Alpha's behavior directives.

Opt-out
-------
Set ``ALPHA_NO_PROJECT_CONTEXT=1`` (or any non-empty value other than
``0``/``false``/``no``) to skip the lookup entirely. Useful in CI or when
debugging prompt issues.

Size cap
--------
The file is capped at ``MAX_BYTES`` (16 KB). Larger files are truncated
with a clear notice so the agent knows part of the context is missing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CONTEXT_FILENAME = "ALPHA.md"
MAX_BYTES = 16 * 1024  # 16 KB — generous for a project README-style file
_TRUNCATION_NOTICE = (
    "\n\n[... ALPHA.md truncated by Alpha — file exceeds {limit} bytes "
    "({size} bytes total). Move long-form notes elsewhere or split the "
    "file to keep the system prompt lean.]"
)


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project context, ready to inject into the system prompt."""

    path: Path
    body: str  # already truncated if needed
    raw_size: int
    truncated: bool


def _is_disabled() -> bool:
    raw = os.environ.get("ALPHA_NO_PROJECT_CONTEXT", "").strip().lower()
    return raw not in ("", "0", "false", "no")


def find_context_file(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` (default: CWD) looking for ``ALPHA.md``.

    Returns the first match's absolute path, or ``None`` if no ancestor
    contains the file. Mirrors ``.git`` discovery semantics.
    """
    cur = (start or Path.cwd()).resolve()
    # ``.parents`` excludes ``cur`` itself, so we check ``cur`` first.
    for candidate in (cur, *cur.parents):
        target = candidate / CONTEXT_FILENAME
        if target.is_file():
            return target
    return None


def load_project_context(start: Path | None = None) -> ProjectContext | None:
    """Find and read the project's ``ALPHA.md``.

    Returns ``None`` when:
    - The opt-out env var is set.
    - No ``ALPHA.md`` is found in any ancestor directory.
    - The file exists but cannot be read (logged at WARNING).
    """
    if _is_disabled():
        return None

    path = find_context_file(start)
    if path is None:
        return None

    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.warning("Could not read project context %s: %s", path, e)
        return None

    raw_size = len(raw)
    truncated = raw_size > MAX_BYTES
    if truncated:
        body_bytes = raw[:MAX_BYTES]
    else:
        body_bytes = raw

    try:
        body = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Be lenient — we'd rather inject lossy text than nothing.
        body = body_bytes.decode("utf-8", errors="replace")

    if truncated:
        body = body.rstrip() + _TRUNCATION_NOTICE.format(
            limit=MAX_BYTES, size=raw_size
        )

    return ProjectContext(
        path=path, body=body, raw_size=raw_size, truncated=truncated
    )


_PROJECT_CONTEXT_PREAMBLE = (
    "The section below was loaded from the user's project at startup. "
    "Treat it as **authoritative, project-specific guidance** that "
    "overrides generic directives above when they conflict. Read it "
    "before answering anything that depends on conventions, layout, "
    "or local rules — for example: which command runs the tests, "
    "where files belong, what tools are out-of-scope here. Refer back "
    "to it across the session; do not forget it after the first turn."
)


def inject_project_context(system_prompt: str, ctx: ProjectContext | None) -> str:
    """Append the project context to a system prompt as a labeled section.

    The injected block carries a short preamble telling the model that
    the project context wins over the generic guidance in the rest of
    the prompt — without that, the model treats both layers with equal
    weight and conflict resolution becomes random.
    """
    if ctx is None:
        return system_prompt
    section = (
        f"# PROJECT CONTEXT (from {CONTEXT_FILENAME})\n"
        f"{_PROJECT_CONTEXT_PREAMBLE}\n\n"
        f"---\n\n"
        f"{ctx.body.strip()}\n"
    )
    return f"{system_prompt.rstrip()}\n\n{section}"
