"""
Conversation history persistence for Alpha Code.

Saves and loads conversation sessions to/from disk as JSON files.
Sessions are stored in ~/.alpha_code/history/ with timestamps.
Supports session summaries for quick resume and context continuity.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_HISTORY_DIR = Path.home() / ".alpha_code" / "history"
_MAX_SESSIONS = 50  # keep last N sessions on disk


def _ensure_dir() -> Path:
    """Create history directory if needed."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return _HISTORY_DIR


def _session_path(session_id: str) -> Path:
    return _ensure_dir() / f"{session_id}.json"


def generate_session_id() -> str:
    """Generate a unique session ID based on timestamp."""
    return time.strftime("%Y%m%d_%H%M%S")


def _build_session_summary(messages: list[dict]) -> str:
    """
    Build a compact summary of the session for continuity.

    Extracts user requests, assistant conclusions, and key tool results
    into a condensed text that can seed a new session.
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "user":
            # Strip CWD prefix
            text = content
            if text.startswith("[CWD:"):
                text = text.split("\n", 1)[-1] if "\n" in text else text
            if text.startswith("[CONTEXT SUMMARY"):
                # Already a summary — include as-is
                parts.append(f"[previous context]: {text[:500]}")
            else:
                parts.append(f"[user]: {text[:300]}")

        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_names = []
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    tc_names.append(fn.get("name", "?"))
                parts.append(f"[assistant called: {', '.join(tc_names)}]")
            if content:
                # Keep last assistant response more fully (it's the conclusion)
                parts.append(f"[assistant]: {content[:500]}")

        elif role == "tool":
            # Very brief tool result summary
            preview = content[:200] if content else "(empty)"
            parts.append(f"[tool result]: {preview}")

    return "\n".join(parts[-40:])  # keep last 40 entries max


def save_session(
    session_id: str, messages: list[dict], metadata: dict | None = None
) -> Path:
    """
    Save conversation messages to disk with a summary for continuity.

    Args:
        session_id: Unique session identifier.
        messages: Full message list (system + user + assistant + tool).
        metadata: Optional metadata (provider, model, etc).

    Returns:
        Path to the saved file.
    """
    # Filter out system messages and truncate tool results for storage
    storable = []
    for msg in messages:
        entry = dict(msg)
        if entry.get("role") == "system":
            continue  # skip system prompt (loaded fresh each time)
        if entry.get("role") == "tool":
            content = entry.get("content", "")
            if len(content) > 2000:
                entry["content"] = content[:2000] + "\n... [truncated for storage]"
        storable.append(entry)

    # Build session summary for quick resume
    summary = _build_session_summary(storable)

    data = {
        "session_id": session_id,
        "timestamp": time.time(),
        "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "message_count": len(storable),
        "summary": summary,
        "messages": storable,
    }
    if metadata:
        data["metadata"] = metadata

    path = _session_path(session_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Session saved: {path}")

    _cleanup_old_sessions()
    return path


def load_session(session_id: str) -> list[dict] | None:
    """
    Load conversation messages from disk.

    Returns message list or None if not found.
    """
    path = _session_path(session_id)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("messages", [])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load session {session_id}: {e}")
        return None


def load_session_summary(session_id: str) -> str | None:
    """
    Load just the session summary for lightweight resume.

    Returns summary string or None if not found.
    """
    path = _session_path(session_id)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("summary")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load session summary {session_id}: {e}")
        return None


def get_last_session_id() -> str | None:
    """Get the most recent session ID, or None if no sessions exist."""
    history_dir = _ensure_dir()
    files = sorted(history_dir.glob("*.json"), reverse=True)
    if not files:
        return None
    return files[0].stem


def list_sessions(limit: int = 20) -> list[dict]:
    """
    List recent sessions sorted by date (newest first).

    Returns list of {session_id, timestamp_human, message_count, preview}.
    """
    history_dir = _ensure_dir()
    sessions = []

    for path in sorted(history_dir.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Find first user message as preview
            preview = ""
            for msg in data.get("messages", []):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    # Strip CWD prefix
                    if content.startswith("[CWD:"):
                        content = content.split("\n", 1)[-1]
                    preview = content[:80]
                    break

            sessions.append({
                "session_id": data.get("session_id", path.stem),
                "timestamp_human": data.get("timestamp_human", ""),
                "message_count": data.get("message_count", 0),
                "preview": preview,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return sessions


def _cleanup_old_sessions():
    """Remove oldest sessions beyond _MAX_SESSIONS."""
    history_dir = _ensure_dir()
    files = sorted(history_dir.glob("*.json"))
    if len(files) > _MAX_SESSIONS:
        for old_file in files[: len(files) - _MAX_SESSIONS]:
            old_file.unlink(missing_ok=True)
            logger.debug(f"Cleaned up old session: {old_file.name}")
