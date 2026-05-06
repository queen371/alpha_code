"""HTTP request tools for ALPHA agent.

Provides safe HTTP client capabilities with SSRF protection.

SECURITY: Private/internal IPs are blocked by default to prevent SSRF.
Request size and timeout are limited. No credential forwarding.
"""

import asyncio
import logging
import ssl as _ssl
from urllib.parse import urlparse, urlunparse

from ..net_utils import (
    is_private_ip_address as _is_private_ip_address,
    validate_url as _validate_url,
    resolve_and_validate as _resolve_and_validate,
)
from . import ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)

_MAX_RESPONSE_SIZE = 1_000_000  # 1MB
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _rewrite_url_with_ip(parsed, resolved_ip: str) -> str:
    """Reconstroi URL trocando hostname pelo IP resolvido.

    urlunparse evita falhas em hostnames uppercase ou IPv6 que `str.replace`
    deixaria passar (DNS rebinding window). IPv6 ganha brackets no netloc.
    """
    ip_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    netloc = f"{ip_for_url}:{parsed.port}" if parsed.port else ip_for_url
    parts = list(parsed)
    parts[1] = netloc
    return urlunparse(parts)


async def _http_request(
    url: str,
    method: str = "GET",
    headers: dict = None,
    body: str = None,
    timeout: int | None = None,
) -> dict:
    """Make an HTTP request."""
    from ..config import TOOL_TIMEOUTS
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("network", 30)

    method = method.upper()
    if method not in _ALLOWED_METHODS:
        return {
            "error": f"Método '{method}' não permitido. Use: {', '.join(sorted(_ALLOWED_METHODS))}"
        }

    # Validate URL (SSRF)
    url_error = _validate_url(url)
    if url_error:
        return {"error": url_error, "blocked": True}

    timeout = min(timeout, 60)

    try:
        import aiohttp

        req_headers = dict(headers) if headers else {}
        req_headers.setdefault("User-Agent", "ALPHA-Agent/1.0")

        parsed = urlparse(url)
        hostname = parsed.hostname

        # Resolver DNS e validar IP ANTES de conectar (previne DNS rebinding)
        try:
            resolved_ip = await _resolve_and_validate(hostname)
        except ValueError as e:
            return {"error": str(e), "blocked": True}

        # Construir URL com IP fixo + header Host para SNI/virtual hosts
        fixed_url = _rewrite_url_with_ip(parsed, resolved_ip)
        req_headers["Host"] = hostname

        # HTTPS: cert e SNI precisam validar contra o hostname original,
        # nao o IP literal usado na conexao. Ssl context + server_hostname
        # garantem isso.
        is_https = parsed.scheme == "https"
        ssl_param = _ssl.create_default_context() if is_https else False

        async with aiohttp.ClientSession() as session:
            req_data = None
            if body and method in ("POST", "PUT", "PATCH"):
                content_type = req_headers.get("Content-Type", "")
                if "json" in content_type or (body.startswith("{") or body.startswith("[")):
                    req_headers.setdefault("Content-Type", "application/json")
                req_data = body

            req_kwargs = dict(
                method=method,
                url=fixed_url,
                headers=req_headers,
                data=req_data,
                allow_redirects=False,  # NÃO seguir automaticamente
                timeout=aiohttp.ClientTimeout(total=timeout),
                ssl=ssl_param,
            )
            if is_https:
                req_kwargs["server_hostname"] = hostname
            resp = await session.request(**req_kwargs)

            # Manual redirect following com re-validação DNS+IP
            redirect_count = 0
            while resp.status in (301, 302, 303, 307, 308) and redirect_count < 5:
                redirect_url = resp.headers.get("Location")
                if not redirect_url:
                    break

                # Resolver URL relativo
                if redirect_url.startswith("/"):
                    port = parsed.port
                    port_str = f":{port}" if port else ""
                    redirect_url = f"{parsed.scheme}://{hostname}{port_str}{redirect_url}"

                # Re-validar DNS + IP do destino do redirect
                redirect_parsed = urlparse(redirect_url)
                redirect_hostname = redirect_parsed.hostname
                if not redirect_hostname:
                    break

                try:
                    redirect_ip = await _resolve_and_validate(redirect_hostname)
                except ValueError as e:
                    return {"error": f"Redirect bloqueado: {e}", "blocked": True}

                fixed_redirect = _rewrite_url_with_ip(redirect_parsed, redirect_ip)
                req_headers["Host"] = redirect_hostname

                redirect_is_https = redirect_parsed.scheme == "https"
                redirect_ssl = (
                    _ssl.create_default_context() if redirect_is_https else False
                )
                redirect_kwargs = dict(
                    method=method,
                    url=fixed_redirect,
                    headers=req_headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    ssl=redirect_ssl,
                )
                if redirect_is_https:
                    redirect_kwargs["server_hostname"] = redirect_hostname
                resp = await session.request(**redirect_kwargs)
                redirect_count += 1

            # Read response with size limit
            raw = await resp.read()
            if len(raw) > _MAX_RESPONSE_SIZE:
                body_text = raw[:_MAX_RESPONSE_SIZE].decode(errors="replace")
                body_text += f"\n... [truncado: resposta > {_MAX_RESPONSE_SIZE // 1000}KB]"
            else:
                body_text = raw.decode(errors="replace")

            resp_headers = dict(resp.headers)

            return {
                "status_code": resp.status,
                "headers": {
                    k: v
                    for k, v in resp_headers.items()
                    if k.lower()
                    in (
                        "content-type",
                        "content-length",
                        "server",
                        "date",
                        "location",
                        "x-request-id",
                        "etag",
                    )
                },
                "body": body_text[:15000],
                "url": str(resp.url),
                "method": method,
            }

    except ImportError:
        # Fallback: use urllib (stdlib)
        return await _http_request_urllib(url, method, headers, body, timeout)
    except TimeoutError:
        return {"error": f"Request excedeu timeout de {timeout}s", "timeout": True}
    except Exception as e:
        return {"error": f"Erro na requisição: {e}"}


import urllib.request as _urllib_request


class _NoRedirectHandler(_urllib_request.HTTPRedirectHandler):
    """Prevent urllib from following redirects (DNS rebinding protection)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def _http_request_urllib(
    url: str, method: str, headers: dict, body: str, timeout: int
) -> dict:
    """Fallback HTTP using urllib (stdlib)."""
    import urllib.error
    import urllib.request

    # Resolve DNS and validate IP BEFORE connecting (prevents DNS rebinding)
    parsed = urlparse(url)
    hostname = parsed.hostname
    resolved_ip = None
    if hostname:
        try:
            resolved_ip = await _resolve_and_validate(hostname)
        except ValueError as e:
            return {"error": str(e), "blocked": True}

    try:
        # Build opener that does NOT follow redirects (DNS rebinding fix)
        opener = urllib.request.build_opener(_NoRedirectHandler)
        # Use resolved IP in URL to prevent DNS rebinding between validate and connect
        if resolved_ip and hostname:
            fixed_url = url.replace(f"://{hostname}", f"://{resolved_ip}", 1)
        else:
            fixed_url = url
        req = urllib.request.Request(fixed_url, method=method)
        req.add_header("User-Agent", "ALPHA-Agent/1.0")
        req.add_header("Host", hostname or "")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        data = body.encode() if body else None

        resp = await asyncio.wait_for(
            asyncio.to_thread(lambda: opener.open(req, data=data, timeout=min(timeout, 30))),
            timeout=timeout + 5,
        )

        resp_body = resp.read(_MAX_RESPONSE_SIZE).decode(errors="replace")
        return {
            "status_code": resp.status,
            "headers": dict(resp.headers),
            "body": resp_body[:15000],
            "url": url,
            "method": method,
        }
    except urllib.error.HTTPError as e:
        return {
            "status_code": e.code,
            "error": str(e.reason),
            "body": e.read(_MAX_RESPONSE_SIZE).decode(errors="replace")[:5000],
        }
    except Exception as e:
        return {"error": str(e)}


register_tool(
    ToolDefinition(
        name="http_request",
        description=(
            "Fazer requisição HTTP (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS). "
            "Suporta headers customizados e body. Proteção SSRF: IPs privados bloqueados. "
            "Útil para testar APIs, verificar endpoints, baixar dados."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL completa (http:// ou https://)",
                },
                "method": {
                    "type": "string",
                    "description": "Método HTTP",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "Headers HTTP como objeto {chave: valor}",
                },
                "body": {
                    "type": "string",
                    "description": "Corpo da requisição (para POST, PUT, PATCH). JSON como string.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout em segundos (máx 60). Padrão: 30",
                    "default": 30,
                },
            },
            "required": ["url"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="network",
        executor=_http_request,
    )
)
