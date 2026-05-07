"""Database query tools for ALPHA agent.

Execute SQL queries on local databases (SQLite, PostgreSQL via connection string).

SECURITY: Read-only mode by default. Write operations require explicit flag.
Only local databases allowed (SSRF protection on connection strings).
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .._security_log import sanitize_for_log
from ..net_utils import is_private_ip as _is_private_ip
from . import ToolDefinition, ToolSafety, register_tool
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)

_MAX_ROWS = 500
_MAX_RESULT_CHARS = 15000

# Pool por connection string para PostgreSQL (#D021-PERF). asyncpg.connect()
# faz handshake TCP+TLS+auth (~150-500ms remoto); cada describe_table/query
# abria conexao nova. Pool reusa, drop em sessao termina.
_pg_pools: dict = {}
_pg_pools_lock = asyncio.Lock()


async def _get_pg_pool(connection: str):
    """Lazy-init pool por connection string."""
    pool = _pg_pools.get(connection)
    if pool is not None:
        return pool
    async with _pg_pools_lock:
        pool = _pg_pools.get(connection)
        if pool is None:
            import asyncpg
            pool = await asyncpg.create_pool(
                connection, min_size=1, max_size=5, command_timeout=30,
            )
            _pg_pools[connection] = pool
    return pool


async def _close_pg_pools() -> None:
    """Cleanup helper — chamar em shutdown da CLI."""
    pools = list(_pg_pools.values())
    _pg_pools.clear()
    for pool in pools:
        try:
            await pool.close()
        except Exception:
            pass

# SQL statements that modify data
_WRITE_PATTERNS = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE)\b",
    re.IGNORECASE,
)

# Dangerous patterns always blocked
_BLOCKED_SQL = [
    r"\bATTACH\b",  # attach external databases
    r"\bDETACH\b",  # detach databases
    r"\bLOAD_EXTENSION\b",  # load extensions
    r"\bPRAGMA\s+.*=",  # write pragmas (read pragmas OK)
]


# Regex para validar nomes de tabela seguros (previne SQL injection em PRAGMA/describe)
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _is_write_query(query: str) -> bool:
    """Check if query modifies data."""
    return bool(_WRITE_PATTERNS.match(query.strip()))


def _is_dangerous_query(sql: str) -> str | None:
    """Retorna mensagem de erro se a query é perigosa, None se OK.

    Multi-statement detector (#030): segue o SQL standard onde a unica
    forma de escapar uma quote dentro de string e doubled-quote (`''`).
    O backslash (`\\'`) NAO escapa em SQL standard (Postgres com
    standard_conforming_strings=on, default desde 9.1; SQLite idem).
    O detector legacy tratava `\\'` como escape, criando bypass via
    `'a\\'; DROP TABLE t; --` (que em SQL standard fecha a string em
    `'a\\'` e abre multi-statement).
    """
    stripped = sql.strip()

    # Bloquear multi-statement (;) fora de strings
    in_string = False
    quote_char = None
    i = 0
    n = len(stripped)
    while i < n:
        c = stripped[i]
        if not in_string:
            if c in ("'", '"'):
                in_string, quote_char = True, c
            elif c == ";" and i < n - 1:
                remaining = stripped[i + 1 :].strip()
                if remaining and not remaining.startswith("--"):
                    return "Multi-statement queries bloqueadas por segurança"
        else:
            # Dentro de string: doubled-quote escapa, qualquer outra ocorrencia
            # da mesma quote fecha. Backslash NAO escapa (SQL standard).
            if c == quote_char:
                if i + 1 < n and stripped[i + 1] == quote_char:
                    i += 2  # skip pair, ainda em string
                    continue
                in_string = False
        i += 1

    # Bloquear CTE + write (WITH ... DELETE/UPDATE/INSERT)
    if re.match(r"\s*WITH\b", stripped, re.I):
        if re.search(r"\b(DELETE|UPDATE|INSERT|DROP|ALTER|TRUNCATE)\b", stripped, re.I):
            return "CTE com operação de escrita requer aprovação"

    return None


def _validate_query(query: str, read_only: bool) -> str | None:
    """Validate SQL query. Returns error message or None."""
    for pattern in _BLOCKED_SQL:
        if re.search(pattern, query, re.IGNORECASE):
            return "Query bloqueada por segurança: padrão perigoso detectado"

    # Check multi-statement and dangerous patterns
    danger = _is_dangerous_query(query)
    if danger:
        return danger

    if read_only and _is_write_query(query):
        return (
            "Query de escrita bloqueada em modo read_only. "
            "Use read_only=false para permitir escrita."
        )

    return None


def _validate_sqlite_path(db_path: str) -> str | None:
    """Ensure SQLite path is within workspace."""
    p = Path(db_path).expanduser().resolve()
    try:
        p.relative_to(AGENT_WORKSPACE)
    except ValueError:
        return f"Banco de dados fora do workspace permitido ({AGENT_WORKSPACE})"
    if not p.exists():
        return f"Arquivo de banco de dados não encontrado: {db_path}"
    return None


async def _query_sqlite(db_path: str, query: str, read_only: bool) -> dict:
    """Execute query on SQLite database."""
    path_error = _validate_sqlite_path(db_path)
    if path_error:
        return {"error": path_error}

    def _execute():
        # Use URI mode=ro for true read-only enforcement (cannot be bypassed)
        if read_only:
            resolved = Path(db_path).expanduser().resolve()
            uri = f"file:{resolved}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(query)

            if _is_write_query(query):
                conn.commit()
                return {
                    "rows_affected": cursor.rowcount,
                    "last_row_id": cursor.lastrowid,
                    "query": query,
                }

            rows = cursor.fetchmany(_MAX_ROWS)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            result_rows = [dict(row) for row in rows]
            total = len(result_rows)

            return {
                "columns": columns,
                "rows": result_rows,
                "row_count": total,
                "truncated": total >= _MAX_ROWS,
                "query": query,
            }
        except sqlite3.Error as e:
            return {"error": f"SQLite error: {e}", "query": query}
        finally:
            conn.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _execute)


async def _query_database(
    connection: str,
    query: str,
    read_only: bool = True,
    db_type: str = "sqlite",
) -> dict:
    """Execute a SQL query on a local database."""
    # Validate query
    query_error = _validate_query(query, read_only)
    if query_error:
        return {"error": query_error, "blocked": True}

    if db_type == "sqlite":
        return await _query_sqlite(connection, query, read_only)

    elif db_type == "postgresql":
        # Validate hostname against private IP ranges (SSRF protection)
        try:
            parsed_url = urlparse(connection)
            pg_hostname = parsed_url.hostname
            if pg_hostname and _is_private_ip(pg_hostname):
                return {
                    "error": f"Conexão a IP privado/interno bloqueada por segurança (SSRF protection): {pg_hostname}",
                    "blocked": True,
                }
        except Exception:
            return {"error": "Connection string inválida para PostgreSQL"}

        try:
            import asyncpg  # noqa: F401  (validation only; pool import lazily)
        except ImportError:
            return {"error": "asyncpg não instalado. Execute: pip install asyncpg"}

        try:
            pool = await asyncio.wait_for(_get_pg_pool(connection), timeout=10)
            async with pool.acquire() as conn:
                if _is_write_query(query):
                    if read_only:
                        return {"error": "Query de escrita bloqueada em modo read_only"}
                    result = await conn.execute(query)
                    return {"result": result, "query": query}
                rows = await conn.fetch(query)
                rows_list = [dict(r) for r in rows[:_MAX_ROWS]]
                columns = list(rows_list[0].keys()) if rows_list else []
                return {
                    "columns": columns,
                    "rows": rows_list,
                    "row_count": len(rows_list),
                    "truncated": len(rows) >= _MAX_ROWS,
                    "query": query,
                }
        except TimeoutError:
            return {"error": "Timeout ao conectar ao PostgreSQL"}
        except Exception as e:
            # asyncpg errors podem incluir o DSN com password no str(e).
            return {"error": sanitize_for_log(f"PostgreSQL error: {e}")}

    return {"error": f"Tipo de banco '{db_type}' não suportado. Use 'sqlite' ou 'postgresql'."}


async def _list_tables(connection: str, db_type: str = "sqlite") -> dict:
    """List all tables in a database."""
    if db_type == "sqlite":
        return await _query_sqlite(
            connection,
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name",
            read_only=True,
        )
    elif db_type == "postgresql":
        return await _query_database(
            connection,
            "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name",
            read_only=True,
            db_type="postgresql",
        )
    return {"error": f"Tipo de banco '{db_type}' não suportado"}


async def _describe_table(connection: str, table: str, db_type: str = "sqlite") -> dict:
    """Describe a table's schema."""
    # Validar nome da tabela contra SQL injection
    if not _SAFE_IDENTIFIER.match(table):
        return {"error": f"Nome de tabela inválido: '{table}'"}

    if db_type == "sqlite":
        return await _query_sqlite(
            connection,
            f"PRAGMA table_info({table})",
            read_only=True,
        )
    elif db_type == "postgresql":
        # SSRF protection for PostgreSQL
        try:
            parsed_url = urlparse(connection)
            pg_hostname = parsed_url.hostname
            if pg_hostname and _is_private_ip(pg_hostname):
                return {
                    "error": f"Conexão a IP privado/interno bloqueada por segurança (SSRF protection): {pg_hostname}",
                    "blocked": True,
                }
        except Exception:
            return {"error": "Connection string inválida para PostgreSQL"}

        # Usar parameterized query para PostgreSQL
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            return {"error": "asyncpg não instalado. Execute: pip install asyncpg"}

        try:
            pool = await asyncio.wait_for(_get_pg_pool(connection), timeout=10)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_name = $1 "
                    "ORDER BY ordinal_position",
                    table,
                )
                rows_list = [dict(r) for r in rows]
                columns = list(rows_list[0].keys()) if rows_list else []
                return {
                    "columns": columns,
                    "rows": rows_list,
                    "row_count": len(rows_list),
                    "query": f"DESCRIBE {table}",
                }
        except TimeoutError:
            return {"error": "Timeout ao conectar ao PostgreSQL"}
        except Exception as e:
            # asyncpg errors podem incluir o DSN com password no str(e).
            return {"error": sanitize_for_log(f"PostgreSQL error: {e}")}

    return {"error": f"Tipo de banco '{db_type}' não suportado"}


register_tool(
    ToolDefinition(
        name="query_database",
        description=(
            "Executar query SQL em banco de dados local (SQLite ou PostgreSQL). "
            "Modo read_only por padrão — queries de escrita precisam de read_only=false. "
            "Limitado a 500 linhas por resultado. ATTACH e LOAD_EXTENSION bloqueados."
        ),
        parameters={
            "type": "object",
            "properties": {
                "connection": {
                    "type": "string",
                    "description": "Para SQLite: caminho do arquivo .db/.sqlite. Para PostgreSQL: connection string",
                },
                "query": {
                    "type": "string",
                    "description": "Query SQL a executar",
                },
                "read_only": {
                    "type": "boolean",
                    "description": "Modo somente leitura (bloqueia INSERT/UPDATE/DELETE). Padrão: true",
                    "default": True,
                },
                "db_type": {
                    "type": "string",
                    "description": "Tipo do banco de dados",
                    "enum": ["sqlite", "postgresql"],
                    "default": "sqlite",
                },
            },
            "required": ["connection", "query"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="database",
        executor=_query_database,
    )
)

register_tool(
    ToolDefinition(
        name="list_tables",
        description="Listar todas as tabelas e views de um banco de dados.",
        parameters={
            "type": "object",
            "properties": {
                "connection": {
                    "type": "string",
                    "description": "Para SQLite: caminho do arquivo .db. Para PostgreSQL: connection string",
                },
                "db_type": {
                    "type": "string",
                    "description": "Tipo do banco de dados",
                    "enum": ["sqlite", "postgresql"],
                    "default": "sqlite",
                },
            },
            "required": ["connection"],
        },
        safety=ToolSafety.SAFE,
        category="database",
        executor=_list_tables,
    )
)

register_tool(
    ToolDefinition(
        name="describe_table",
        description="Descrever o schema de uma tabela (colunas, tipos, constraints).",
        parameters={
            "type": "object",
            "properties": {
                "connection": {
                    "type": "string",
                    "description": "Para SQLite: caminho do arquivo .db. Para PostgreSQL: connection string",
                },
                "table": {
                    "type": "string",
                    "description": "Nome da tabela",
                },
                "db_type": {
                    "type": "string",
                    "description": "Tipo do banco de dados",
                    "enum": ["sqlite", "postgresql"],
                    "default": "sqlite",
                },
            },
            "required": ["connection", "table"],
        },
        safety=ToolSafety.SAFE,
        category="database",
        executor=_describe_table,
    )
)
