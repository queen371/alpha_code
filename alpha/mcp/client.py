"""Stdio JSON-RPC client for a single MCP server.

The client spawns a subprocess, performs the MCP `initialize` handshake,
discovers tools via `tools/list`, and exposes `call_tool()` for execution.

Implementation note: uses a sync `subprocess.Popen` + reader thread instead
of `asyncio.subprocess`. The CLI recreates an event loop per turn
(`asyncio.run` in main.py), which would orphan asyncio streams bound to a
prior loop. A sync Popen survives across loops cleanly; async callers reach
it via `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_REQUEST_TIMEOUT = 30.0
TOOL_CALL_TIMEOUT = 120.0


class MCPError(RuntimeError):
    """MCP-level error (server returned an error response or transport died)."""


class MCPClient:
    """Connection to one MCP server over stdio."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.extra_env = dict(env or {})
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._inbox: queue.Queue = queue.Queue()
        self._notifications: list[dict] = []  # buffered server-initiated msgs (#007)
        self._notifications_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_id = 1

    # ── Lifecycle ──

    def start(self) -> None:
        merged_env = {**os.environ, **self.extra_env}
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                text=True,
                bufsize=1,  # line-buffered
            )
        except (FileNotFoundError, PermissionError) as e:
            raise MCPError(f"Cannot spawn MCP server '{self.name}': {e}") from e

        self._reader = threading.Thread(
            target=self._read_stdout, name=f"mcp-{self.name}-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name=f"mcp-{self.name}-stderr", daemon=True
        )
        self._stderr_reader.start()

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.join(timeout=2)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=2)
        self.proc = None

    # ── Protocol ──

    def initialize(self) -> dict:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "alpha-code", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized")
        return result or {}

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list") or {}
        self.tools = result.get("tools", []) or []
        return self.tools

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=TOOL_CALL_TIMEOUT,
        ) or {}

    # ── Internals ──

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("MCP %s: non-JSON line discarded: %r", self.name, line[:200])
                continue
            self._inbox.put(msg)
        # EOF: signal disconnection.
        self._inbox.put(None)

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                logger.debug("MCP %s [stderr]: %s", self.name, line)

    def _send(self, message: dict) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.stdin.closed:
            raise MCPError(f"MCP server '{self.name}' is not running")
        payload = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPError(f"MCP server '{self.name}' write failed: {e}") from e

    def _request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict | None:
        # AUDIT_V1.2 #007: release lock after send — holding it during the
        # entire wait loop serialized all RPCs to the same server.
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            req: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                req["params"] = params
            self._send(req)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP '{self.name}' timed out on '{method}' after {timeout}s"
                )
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(
                    f"MCP '{self.name}' timed out on '{method}' after {timeout}s"
                ) from None

            if msg is None:
                raise MCPError(
                    f"MCP '{self.name}' closed connection during '{method}'"
                )

            # Server-initiated notifications (no id) — buffer, don't drop.
            msg_id = msg.get("id")
            if msg_id is None:
                with self._notifications_lock:
                    self._notifications.append(msg)
                continue

            if msg_id != req_id:
                # Response for a different request (concurrent RPCs now
                # possible since lock is released after send). Put it
                # back so the other caller can pick it up.
                self._inbox.put(msg)
                continue

            if "error" in msg:
                err = msg["error"]
                raise MCPError(
                    f"MCP '{self.name}' returned error on '{method}': "
                    f"{err.get('code')} {err.get('message')}"
                )
            return msg.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            msg: dict = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)
