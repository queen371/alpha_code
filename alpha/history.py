"""
Conversation history persistence for Alpha Code.

Saves and loads conversation sessions to/from disk as JSON files.
Sessions are stored in ~/.alpha_code/history/ with timestamps.
Supports session summaries for quick resume and context continuity.
"""

import json
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_HISTORY_DIR = Path.home() / ".alpha_code" / "history"
_MAX_SESSIONS = 50  # keep last N sessions on disk


def _ensure_dir() -> Path:
    """Create history directory if needed.

    Perms 0o700: session files contem tool results (read_file de .env,
    output de execute_shell, query_database rows) e [CWD] do user. Em
    hosts compartilhados, deixar group/other-readable e leak.
    """
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_HISTORY_DIR, 0o700)
    except OSError:
        pass
    return _HISTORY_DIR


def _atomic_write(path: Path, data: str) -> None:
    """Write file with mode 0o600 from creation, no follow-symlink.

    Substitui `path.write_text(...)` que herda umask (default 0o644),
    expondo o conteudo entre usuarios em hosts compartilhados.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    # Idempotente caso o arquivo ja existisse com perms erradas.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _session_path(session_id: str) -> Path:
    return _ensure_dir() / f"{session_id}.json"


def generate_session_id() -> str:
    """Generate a unique session ID: timestamp + random suffix.

    Sufixo de 8 hex evita colisao quando dois agents/REPLs (ou parent+sub-agent)
    sao iniciados no mesmo segundo — sem o sufixo, o segundo `save_session`
    sobrescreve o primeiro silenciosamente, perdendo auditoria/forense.
    """
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"


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


def _sanitize_for_save(messages: list[dict]) -> list[dict]:
    """Remove tool_call/tool message tuples that ficaram orfas.

    Cenario tipico: Ctrl+C entre o `assistant` que emite `tool_calls` e a
    `tool` response correspondente. Sem sanitizacao, a sessao salva fica
    com `assistant.tool_calls` sem responses (ou `tool` orfa) e o proximo
    `/load` quebra com HTTP 400 porque a API valida o emparelhamento.

    Estrategia:
    - Drop `tool` messages cujo `tool_call_id` nao bate com nenhum
      `assistant.tool_calls` anterior nao-respondido.
    - Drop o ultimo `assistant.tool_calls` se nao tiver respostas
      correspondentes ainda.
    """
    if not messages:
        return messages

    cleaned: list[dict] = []
    pending_ids: set[str] = set()

    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            cleaned.append(m)
            pending_ids = {tc.get("id") for tc in m["tool_calls"] if tc.get("id")}
        elif role == "tool":
            tc_id = m.get("tool_call_id")
            if tc_id in pending_ids:
                cleaned.append(m)
                pending_ids.discard(tc_id)
            # tool com id desconhecido: ja era orfa antes do save — drop.
        else:
            cleaned.append(m)

    # Drop final assistant.tool_calls sem responses correspondentes.
    if pending_ids:
        for i in range(len(cleaned) - 1, -1, -1):
            entry = cleaned[i]
            if entry.get("role") == "assistant" and entry.get("tool_calls"):
                if any(
                    tc.get("id") in pending_ids
                    for tc in entry["tool_calls"]
                ):
                    cleaned.pop(i)
                    break
    return cleaned


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
    # Sanitize first: remove orphan tool_calls/tool tuples (Ctrl+C race).
    messages = _sanitize_for_save(messages)

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
    try:
        _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError as e:
        # Disco cheio / permissao / NFS hiccup nao deve derrubar o REPL.
        # Loga e retorna o path mesmo sem persistir — o caller decide se
        # tenta /save de novo ou aceita perda da sessao (#014/#D009).
        logger.warning(f"save_session failed ({path}): {e}")
        return path
    logger.info(f"Session saved: {path}")

    try:
        _cleanup_old_sessions()
    except OSError as e:
        logger.debug(f"_cleanup_old_sessions failed: {e}")
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
