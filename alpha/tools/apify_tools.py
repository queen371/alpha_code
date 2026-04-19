"""Apify integration — run any Actor and collect results."""

import json
import logging
import os
import asyncio

import httpx

from . import ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL = 5  # seconds between status checks
_MAX_WAIT = 300  # max seconds to wait for an Actor run


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

    # Start the Actor run
    async with httpx.AsyncClient(timeout=30) as client:
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

        run_data = resp.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data.get("defaultDatasetId")

        logger.info(f"Apify Actor run started: {run_id}")

        # Poll until finished
        elapsed = 0
        while elapsed < _MAX_WAIT:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

            try:
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers,
                )
                status_resp.raise_for_status()
            except httpx.HTTPError:
                continue

            status = status_resp.json()["data"]["status"]

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

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{APIFY_BASE}/store",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return {"error": f"Failed to list actors: {e}"}

        data = resp.json()["data"]
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
        safety=ToolSafety.SAFE,
        executor=_run_actor,
        category="scraping",
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
        category="scraping",
    )
)
