"""Apify integration — run any Actor and collect results."""

import json
import logging
import os
import asyncio

import httpx

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL = 5  # seconds between status checks
_MAX_WAIT = 300  # max seconds to wait for an Actor run

# #D012: client compartilhado entre _run_actor e _list_actors. Antes
# cada chamada criava `async with httpx.AsyncClient(...)` — handshake
# TCP/TLS por call (~150-300ms). Reuso mantem keep-alive ativo.
_shared_apify_client: httpx.AsyncClient | None = None
_apify_client_loop: object | None = None


def _get_apify_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """Lazy single-instance client. Recria se loop atual difere do que criou."""
    global _shared_apify_client, _apify_client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        _shared_apify_client is None
        or _shared_apify_client.is_closed
        or _apify_client_loop is not loop
    ):
        _shared_apify_client = httpx.AsyncClient(timeout=timeout)
        _apify_client_loop = loop
    return _shared_apify_client


def _get_token() -> str:
    token = os.getenv("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError(
            "APIFY_API_TOKEN not set. Get your token at https://console.apify.com/account#/integrations"
        )
    return token


async def _run_actor(
    actor_id: str,
    input_data: str = "{}",
    max_items: int = 50,
    memory_mbytes: int = 256,
    timeout_secs: int = 120,
    build: str = "latest",
) -> dict:
    """Run an Apify Actor and return its dataset items."""
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        run_input = json.loads(input_data) if isinstance(input_data, str) else input_data
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in input_data: {e}"}

    # Start the Actor run via shared client (#D012)
    client = _get_apify_client(timeout=30.0)
    try:
        resp = await client.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            headers=headers,
            json=run_input,
            params={
                "memory": memory_mbytes,
                "timeout": timeout_secs,
                "build": build,
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return {"error": f"Failed to start Actor: {e.response.status_code} — {e.response.text}"}
    except httpx.RequestError as e:
        return {"error": f"Request failed: {e}"}

    # #D026: parse defensivo. Apify pode retornar shape diferente em
    # token expirado, rate limit, ou breaking change da API. Sem isso,
    # KeyError sobe cru ate o executor.
    try:
        payload = resp.json()
    except ValueError as e:
        return {"error": f"Apify returned non-JSON response: {e}"}
    run_data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(run_data, dict):
        return {"error": f"Apify response shape inesperada: {str(payload)[:200]}"}
    run_id = run_data.get("id")
    if not run_id:
        return {"error": f"Apify response sem run id: {str(payload)[:200]}"}
    dataset_id = run_data.get("defaultDatasetId")

    logger.info(f"Apify Actor run started: {run_id}")

    # Poll until finished
    elapsed = 0
    consecutive_errors = 0
    last_error: str | None = None
    while elapsed < _MAX_WAIT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL

        try:
            status_resp = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                headers=headers,
            )
            status_resp.raise_for_status()
        except httpx.HTTPError as e:
            # #051/#D012: antes era `continue` silencioso. Loggar +
            # contar consecutivos para abortar se o endpoint estiver
            # offline (em vez de pollar inutilmente ate _MAX_WAIT).
            consecutive_errors += 1
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(
                f"Apify poll error ({elapsed}s elapsed, "
                f"{consecutive_errors} consecutive): {last_error}"
            )
            if consecutive_errors >= 5:
                return {
                    "error": (
                        f"Apify polling failed {consecutive_errors}x consecutive. "
                        f"Last error: {last_error}"
                    ),
                    "run_id": run_id,
                }
            continue
        consecutive_errors = 0  # sucesso reseta contador

        try:
            status_payload = status_resp.json()
            status = status_payload.get("data", {}).get("status")
        except ValueError:
            status = None
        if status is None:
            # Status JSON malformado — tratar como soft error e retentar
            consecutive_errors += 1
            logger.warning(
                f"Apify status response malformed ({consecutive_errors}x consecutive)"
            )
            continue

        if status == "SUCCEEDED":
            break
        elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
            return {"error": f"Actor run {status}", "run_id": run_id}
    else:
        return {"error": f"Timeout waiting for Actor (>{_MAX_WAIT}s)", "run_id": run_id}

    # Fetch dataset items
    if not dataset_id:
        return {"status": "SUCCEEDED", "run_id": run_id, "items": []}

    try:
        items_resp = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=headers,
            params={"limit": max_items, "format": "json"},
        )
        items_resp.raise_for_status()
        items = items_resp.json()
    except httpx.HTTPError as e:
        return {"error": f"Failed to fetch results: {e}", "run_id": run_id}
    except ValueError as e:
        return {"error": f"Apify items endpoint returned non-JSON: {e}", "run_id": run_id}
    if not isinstance(items, list):
        items = []

    return {
        "status": "SUCCEEDED",
        "run_id": run_id,
        "dataset_id": dataset_id,
        "total_items": len(items),
        "items": items,
    }


async def _list_actors(
    search: str = "",
    limit: int = 10,
) -> dict:
    """Search the Apify Store for available Actors."""
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    params = {"limit": limit}
    if search:
        params["search"] = search

    client = _get_apify_client(timeout=15.0)
    try:
        resp = await client.get(
            f"{APIFY_BASE}/store",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return {"error": f"Failed to list actors: {e}"}

    try:
        payload = resp.json()
    except ValueError as e:
        return {"error": f"Apify returned non-JSON: {e}"}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    actors = [
        {
            "id": a.get("username", "") + "/" + a.get("name", ""),
            "title": a.get("title", ""),
            "description": a.get("description", "")[:200],
            "runs": a.get("stats", {}).get("totalRuns", 0),
        }
        for a in data.get("items", [])
    ]

    return {"total": len(actors), "actors": actors}


# ── Registration ──────────────────────────────────────────────

register_tool(
    ToolDefinition(
        name="apify_run_actor",
        description=(
            "Run any Apify Actor to scrape/extract data from websites. "
            "Provide the Actor ID (e.g. 'apify/web-scraper') and input JSON. "
            "Returns the scraped dataset items. "
            "Browse available Actors at https://apify.com/store or use apify_search_actors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "actor_id": {
                    "type": "string",
                    "description": (
                        "Actor ID in format 'username/actor-name' "
                        "(e.g. 'apify/web-scraper', 'apify/website-content-crawler')"
                    ),
                },
                "input_data": {
                    "type": "string",
                    "description": (
                        "JSON string with Actor input parameters. "
                        "Each Actor has different inputs — check its docs. "
                        'Example: \'{"startUrls": [{"url": "https://example.com"}]}\''
                    ),
                    "default": "{}",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Max items to return from the dataset (default: 50)",
                    "default": 50,
                },
                "memory_mbytes": {
                    "type": "integer",
                    "description": "Memory allocation in MB (default: 256)",
                    "default": 256,
                },
                "timeout_secs": {
                    "type": "integer",
                    "description": "Actor timeout in seconds (default: 120)",
                    "default": 120,
                },
            },
            "required": ["actor_id"],
        },
        # Actor arbitrario com input arbitrario = exfil/SSRF indireto via
        # actors maliciosos no marketplace. Requer aprovacao humana.
        safety=ToolSafety.DESTRUCTIVE,
        executor=_run_actor,
        category=ToolCategory.SCRAPING,
    )
)

register_tool(
    ToolDefinition(
        name="apify_search_actors",
        description=(
            "Search the Apify Store for available Actors/scrapers. "
            "Use this to find the right Actor for a scraping task "
            "(e.g. search 'tiktok' to find TikTok scrapers)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Search query (e.g. 'instagram', 'google maps', 'e-commerce')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 10)",
                    "default": 10,
                },
            },
            "required": ["search"],
        },
        safety=ToolSafety.SAFE,
        executor=_list_actors,
        category=ToolCategory.SCRAPING,
    )
)
