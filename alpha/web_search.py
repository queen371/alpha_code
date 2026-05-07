"""
Web Search + Content Extraction for Alpha_Code.

Provider: DuckDuckGo (free, default).
Extraction: trafilatura (primary), HTML strip (fallback).
"""

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx

from .net_utils import (
    is_private_ip as _is_private_ip,
    resolve_and_validate as _resolve_and_validate,
)

logger = logging.getLogger(__name__)

__all__ = [
    "search_duckduckgo",
    "search_multiple_queries",
    "extract_page_content",
    "extract_multiple_pages",
]

MAX_DOWNLOAD_BYTES = 1 * 1024 * 1024  # 1MB per page

# Default timeout for web fetches
_TIMEOUT = httpx.Timeout(connect=5, read=15, write=5, pool=5)


async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """
    Busca no DuckDuckGo via thread executor (lib é síncrona).
    Retorna lista de dicts com keys: title, href, body (snippet).
    """
    from ddgs import DDGS

    def _search():
        return list(DDGS().text(query, max_results=max_results))

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search),
            timeout=30.0,
        )
        return results
    except TimeoutError:
        logger.error(f"DuckDuckGo search timed out for '{query}' (30s)")
        return []
    except Exception as e:
        logger.error(f"DuckDuckGo search failed for '{query}': {e}")
        return []


async def search_multiple_queries(
    queries: list[str],
    max_results_per_query: int = 5,
) -> list[dict]:
    """
    Executa múltiplas queries em paralelo. Deduplica por URL.
    """
    search_fn = search_duckduckgo

    tasks = [search_fn(q, max_results_per_query) for q in queries]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_urls: set[str] = set()
    combined: list[dict] = []

    for result_set in all_results:
        if isinstance(result_set, Exception):
            logger.warning(f"Search query failed: {result_set}")
            continue
        for r in result_set:
            url = r.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                combined.append(r)

    return combined


_REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Reusable httpx client (avoids TCP+TLS handshake per URL)
_shared_client: httpx.AsyncClient | None = None
_client_loop: object | None = None


async def _get_shared_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client.

    httpx.AsyncClient amarra o transport ao loop em que foi criado. Quando
    o CLI roda em modo single-shot, cada `python main.py "task"` cria um
    loop novo via `asyncio.run()` mas o modulo persiste se a CLI for
    re-importada (testes, daemon mode). Detectar `is_closed` nao basta —
    o client pode estar `not closed` mas amarrado a um loop morto, gerando
    `RuntimeError: Event loop is closed` na proxima request.
    """
    global _shared_client, _client_loop
    import asyncio

    loop = asyncio.get_running_loop()
    if (
        _shared_client is None
        or _shared_client.is_closed
        or _client_loop is not loop
    ):
        # Loop diferente: tenta fechar o client antigo se ainda esta vivo.
        # Aclose no loop errado pode falhar — engolimos.
        if _shared_client is not None and not _shared_client.is_closed:
            try:
                await _shared_client.aclose()
            except Exception:
                pass
        _shared_client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=_TIMEOUT,
        )
        _client_loop = loop
    return _shared_client


def _build_pinned_url(parsed, resolved_ip: str) -> str:
    """Reconstroi URL trocando hostname pelo IP resolvido (urlunparse para
    cobrir uppercase + IPv6 em vez de str.replace fragil)."""
    from urllib.parse import urlunparse

    ip_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    netloc = f"{ip_for_url}:{parsed.port}" if parsed.port else ip_for_url
    parts = list(parsed)
    parts[1] = netloc
    return urlunparse(parts)


async def _fetch_raw(url: str, timeout: float, max_bytes: int) -> tuple[bytes, dict[str, str], int]:
    """
    Fetch raw bytes from URL using httpx.
    Returns (raw_bytes, headers_dict, status_code).

    Pin de IP contra DNS rebinding (#D106-SEC): resolve hostname uma vez
    via `_resolve_and_validate`, conecta no IP literal, e usa
    `extensions={"sni_hostname": hostname}` para que cert/SNI continuem
    validando contra o hostname original — mantendo HTTPS funcional.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        return b"", {}, 0

    try:
        resolved_ip = await _resolve_and_validate(hostname)
    except ValueError as e:
        logger.warning(f"SSRF blocked for {url}: {e}")
        return b"", {}, 0

    try:
        client = await _get_shared_client()
        pinned_url = _build_pinned_url(parsed, resolved_ip)
        req_headers = {**_REQ_HEADERS, "Host": hostname}
        ext = {"sni_hostname": hostname} if parsed.scheme == "https" else None
        resp = await client.get(pinned_url, headers=req_headers, extensions=ext)

        # Manual redirect following with SSRF re-validation
        redirect_count = 0
        while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 5:
            location = resp.headers.get("location")
            if not location:
                break
            redirect_parsed = urlparse(location)
            redirect_host = redirect_parsed.hostname or ""
            if not redirect_host:
                break
            try:
                redirect_ip = await _resolve_and_validate(redirect_host)
            except ValueError as e:
                logger.warning(f"SSRF blocked: redirect to {location}: {e}")
                return b"", {}, 0
            pinned_redirect = _build_pinned_url(redirect_parsed, redirect_ip)
            redirect_headers = {**_REQ_HEADERS, "Host": redirect_host}
            redirect_ext = (
                {"sni_hostname": redirect_host}
                if redirect_parsed.scheme == "https" else None
            )
            resp = await client.get(
                pinned_redirect,
                headers=redirect_headers,
                extensions=redirect_ext,
            )
            redirect_count += 1

        if resp.status_code >= 400:
            return b"", {}, resp.status_code

        raw = resp.content
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
            logger.warning(f"Response too large for {url}, truncating at {max_bytes} bytes")

        return raw, dict(resp.headers), resp.status_code
    except Exception as e:
        logger.debug(f"httpx fetch failed for {url}: {e}")
        return b"", {}, 0


async def extract_page_content(url: str, timeout: float = 10.0, max_chars: int = 8000) -> str:
    """
    Busca URL e extrai texto principal.
    Tenta trafilatura primeiro, fallback para strip HTML básico.
    """
    raw, headers, status = await _fetch_raw(url, timeout, MAX_DOWNLOAD_BYTES)
    if not raw:
        return ""

    html = raw.decode("utf-8", errors="replace")

    # Trafilatura (best quality)
    text = ""
    try:
        import trafilatura

        text = await asyncio.to_thread(trafilatura.extract, html) or ""
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Trafilatura failed for {url}: {e}")

    # Fallback: strip HTML
    if not text:
        text = _strip_html(html)

    return text[:max_chars]


# Pre-compiladas: o cache interno do `re` (limite 512) podia evictar essas em
# codebase grande, e re-compilar `<(script|style)...>` com flags DOTALL+ICASE
# em hot path de extract_multiple_pages multiplicava o custo (#D014-PERF).
_RE_SCRIPT_STYLE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Remove tags HTML e colapsa whitespace.

    `html.unescape` decodifica entidades (&amp; &lt; &#39; etc) que sobravam
    como literais no texto extraido — atrapalhava grep e injetava ruido em
    extracoes que viravam input do LLM (#029).
    """
    import html as _html

    html = _RE_SCRIPT_STYLE.sub("", html)
    text = _RE_TAGS.sub(" ", html)
    text = _html.unescape(text)
    return _RE_WS.sub(" ", text).strip()


async def extract_multiple_pages(
    urls: list[str], max_concurrent: int = 6, max_chars_per_page: int = 8000
) -> dict[str, str]:
    """
    Extrai conteúdo de múltiplas URLs concorrentemente.
    Retorna dict url -> texto extraído.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(url: str) -> tuple[str, str]:
        async with semaphore:
            text = await extract_page_content(url, max_chars=max_chars_per_page)
            return url, text

    tasks = [_fetch_one(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    url_to_text: dict[str, str] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        url, text = result
        if text:
            url_to_text[url] = text

    return url_to_text
