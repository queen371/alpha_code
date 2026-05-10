"""Web search tool for ALPHA agent (direct mode).

Exposes web search as a callable tool so the LLM can search on demand
instead of relying on the pre-loop web search phase.
"""

import logging

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 40_000


async def _web_search(
    query: str,
    max_results: int = 5,
    extract_content: bool = True,
) -> dict:
    """Search the web and optionally extract page content."""
    from ..web_search import extract_multiple_pages, search_multiple_queries

    try:
        raw_results = await search_multiple_queries(
            [query], max_results_per_query=min(max_results, 10)
        )

        if not raw_results:
            return {"results": [], "message": "Nenhum resultado encontrado."}

        results = []
        for r in raw_results[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

        # Optionally extract full page content
        if extract_content and raw_results:
            urls = [r["href"] for r in raw_results[:6] if r.get("href")]
            url_to_text = await extract_multiple_pages(urls)

            total = 0
            for item in results:
                url = item["url"]
                content = url_to_text.get(url, "")
                if content:
                    remaining = _MAX_CONTEXT_CHARS - total
                    if remaining <= 0:
                        break
                    item["content"] = content[:remaining]
                    total += len(item["content"])

        return {
            "results": results,
            "total": len(results),
        }

    except Exception as e:
        logger.warning(f"web_search tool error: {e}")
        return {"error": str(e)}


register_tool(
    ToolDefinition(
        name="web_search",
        description=(
            "Search the web using DuckDuckGo and extract page content. "
            "Use this to find current information, answer factual questions, "
            "research topics, or look up documentation. "
            "Returns titles, URLs, snippets, and optionally full page text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (be specific for better results)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5, max: 10)",
                    "default": 5,
                },
                "extract_content": {
                    "type": "boolean",
                    "description": "Whether to extract full page content (default: true)",
                    "default": True,
                },
            },
            "required": ["query"],
        },
        safety=ToolSafety.SAFE,
        executor=_web_search,
        category=ToolCategory.SEARCH,
    )
)
