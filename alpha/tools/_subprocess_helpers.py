"""Safe subprocess runner — centralizes CancelledError/TimeoutError handling.

Extracted from shell_tools, code_tools, pipeline_tools, git_tools (#D001).
~120 linhas duplicadas em 8+ sites reduzidas para ~1 chamada por site.
"""

import asyncio
import logging

from .safe_env import get_safe_env

logger = logging.getLogger(__name__)


class SubprocessResult:
    """Result of run_subprocess_safe()."""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class SubprocessTimeoutError(TimeoutError):
    """Raised when subprocess exceeds timeout. Carries partial output."""
    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"Subprocess timed out after {timeout}s")


async def run_subprocess_safe(
    *cmd: str,
    timeout: float = 30,
    cwd: str | None = None,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Run a subprocess with mandatory CancelledError/TimeoutError safety.

    Every site that previously called create_subprocess_exec + communicate
    must use this helper. It guarantees:
    - TimeoutError: proc.kill() + wait() → SubprocessTimeoutError raised
    - CancelledError/KeyboardInterrupt: proc.kill() + wait() → re-raised
    - O subprocess nunca fica orfao rodando apos o caller cancelar.

    Returns SubprocessResult with returncode, stdout, stderr on success.
    """
    if env is None:
        env = get_safe_env()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise SubprocessTimeoutError(timeout) from None
    except (asyncio.CancelledError, KeyboardInterrupt):
        proc.kill()
        await proc.wait()
        raise

    return SubprocessResult(
        returncode=proc.returncode or 0,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )
