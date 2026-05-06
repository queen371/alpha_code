"""Browser automation tools (Playwright).

Persistent browser session shared across calls. Read-only operations
(navigate, get_content, screenshot) are SAFE; interaction (click, fill,
execute_js) is DESTRUCTIVE and requires user approval.
"""

import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from . import ToolDefinition, ToolSafety, register_tool
from .browser_session import (
    PLAYWRIGHT_AVAILABLE,
    BrowserSession,
    validate_browser_url,
)

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 50_000
_MAX_QUERY_RESULTS = 50
_MAX_DESCRIBE_ELEMENTS = 100


def _check_available() -> dict | None:
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "error": (
                "Playwright not installed. Run: "
                "pip install playwright && playwright install chromium"
            )
        }
    return None


def _domain_allowed(url: str) -> str | None:
    from ..config import (
        BROWSER_DOMAIN_ALLOWLIST,
        BROWSER_DOMAIN_BLOCKLIST,
        BROWSER_REQUIRE_ALLOWLIST,
    )

    host = (urlparse(url).hostname or "").lower()
    for blocked in BROWSER_DOMAIN_BLOCKLIST:
        if host == blocked or host.endswith("." + blocked):
            return f"Domínio '{host}' está na blocklist"
    if BROWSER_DOMAIN_ALLOWLIST:
        for allowed in BROWSER_DOMAIN_ALLOWLIST:
            if host == allowed or host.endswith("." + allowed):
                return None
        return f"Domínio '{host}' fora da allowlist"
    # Allowlist vazia: fail-closed se o operador exigir allowlist explicita.
    if BROWSER_REQUIRE_ALLOWLIST:
        return (
            "Allowlist vazia e ALPHA_BROWSER_REQUIRE_ALLOWLIST=1: defina "
            "ALPHA_BROWSER_ALLOWLIST=dominio1,dominio2 antes de navegar."
        )
    return None


async def _ensure_session(headless: bool = True):
    session = await BrowserSession.get()
    if not session.is_open():
        await session.open(headless=headless)
    return session


async def _require_page():
    """Returns (page, error_dict_or_None)."""
    err = _check_available()
    if err:
        return None, err
    session = await BrowserSession.get()
    if not session.is_open():
        return None, {"error": "Sessão de navegador não aberta. Use browser_open primeiro."}
    page = session.page
    if page is None:
        return None, {"error": "Sem aba ativa na sessão."}
    return page, None


# ─── Session lifecycle ───────────────────────────────────────────


async def _browser_open(headless: bool = True) -> dict:
    err = _check_available()
    if err:
        return err
    try:
        session = await BrowserSession.get()
        already_open = session.is_open()
        if not already_open:
            await session.open(headless=headless)
        return {
            "status": "already_open" if already_open else "opened",
            "headless": session.headless,
            "tab_count": len(session.pages),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def _browser_close() -> dict:
    err = _check_available()
    if err:
        return err
    session = await BrowserSession.get()
    if not session.is_open():
        return {"status": "not_open"}
    await session.close()
    return {"status": "closed"}


async def _browser_status() -> dict:
    err = _check_available()
    if err:
        return err
    session = await BrowserSession.get()
    if not session.is_open():
        return {"open": False}
    page = session.page
    return {
        "open": True,
        "url": page.url if page else "",
        "title": (await page.title()) if page else "",
        "tab_count": len(session.pages),
        "active_tab": session.active_idx,
        "headless": session.headless,
    }


# ─── Navigation ──────────────────────────────────────────────────


async def _browser_navigate(url: str, wait_until: str = "load", timeout: int = 30) -> dict:
    err = _check_available()
    if err:
        return err
    url_err = validate_browser_url(url) or _domain_allowed(url)
    if url_err:
        return {"error": url_err, "blocked": True}
    try:
        session = await _ensure_session()
        page = session.page
        if wait_until not in ("load", "domcontentloaded", "networkidle", "commit"):
            wait_until = "load"
        resp = await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
        return {
            "url": page.url,
            "status_code": resp.status if resp else None,
            "title": await page.title(),
        }
    except Exception as e:
        return {"error": f"Navegação falhou: {type(e).__name__}: {e}"}


async def _browser_back() -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.go_back()
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


async def _browser_forward() -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.go_forward()
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


async def _browser_reload() -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.reload()
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


# ─── Reading ─────────────────────────────────────────────────────


async def _browser_get_content(format: str = "text") -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        if format == "html":
            content = await page.content()
        else:
            content = await page.inner_text("body")
        truncated = len(content) > _MAX_CONTENT_CHARS
        if truncated:
            content = content[:_MAX_CONTENT_CHARS]
        return {
            "url": page.url,
            "title": await page.title(),
            "format": format,
            "content": content,
            "truncated": truncated,
        }
    except Exception as e:
        return {"error": str(e)}


async def _browser_screenshot(save_to: str | None = None, full_page: bool = False) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        from ..config import AGENT_WORKSPACE

        if not save_to:
            save_to = f"browser_screenshot_{int(time.time())}.png"
        path = Path(save_to)
        if not path.is_absolute():
            base = Path(AGENT_WORKSPACE) if AGENT_WORKSPACE else Path.cwd()
            path = base / save_to
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=full_page)
        return {
            "saved_to": str(path),
            "size_bytes": path.stat().st_size,
            "url": page.url,
        }
    except Exception as e:
        return {"error": str(e)}


_DESCRIBE_JS = """(maxItems) => {
    const sel = 'a, button, input, select, textarea, [role=button], [role=link], [role=textbox], [role=combobox], [role=menuitem]';
    const items = [];
    let count = 0;
    document.querySelectorAll(sel).forEach((el) => {
        if (count >= maxItems) return;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        const tag = el.tagName.toLowerCase();
        let selector = '';
        if (el.id) selector = '#' + CSS.escape(el.id);
        else if (el.name) selector = `${tag}[name="${el.name}"]`;
        else if (el.getAttribute('data-testid')) selector = `[data-testid="${el.getAttribute('data-testid')}"]`;
        items.push({
            tag,
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            aria_label: el.getAttribute('aria-label') || '',
            placeholder: el.placeholder || '',
            text: (el.innerText || el.value || '').slice(0, 80).trim(),
            href: el.href || '',
            selector,
        });
        count++;
    });
    return items;
}"""


async def _browser_describe_page() -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        elements = await page.evaluate(_DESCRIBE_JS, _MAX_DESCRIBE_ELEMENTS)
        return {
            "url": page.url,
            "title": await page.title(),
            "elements": elements,
            "count": len(elements),
        }
    except Exception as e:
        return {"error": str(e)}


async def _browser_query(selector: str, attribute: str | None = None) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        elements = await page.query_selector_all(selector)
        if not elements:
            return {"selector": selector, "matches": [], "count": 0}
        results = []
        for el in elements[:_MAX_QUERY_RESULTS]:
            entry = {
                "text": (await el.inner_text())[:300],
                "visible": await el.is_visible(),
            }
            if attribute:
                entry[attribute] = await el.get_attribute(attribute)
            results.append(entry)
        return {"selector": selector, "matches": results, "count": len(elements)}
    except Exception as e:
        return {"error": str(e)}


async def _browser_wait_for(selector: str, timeout: int = 10) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.wait_for_selector(selector, timeout=timeout * 1000)
        return {"selector": selector, "found": True}
    except Exception as e:
        return {"selector": selector, "found": False, "error": str(e)}


# ─── Tab management ──────────────────────────────────────────────


async def _browser_list_tabs() -> dict:
    err = _check_available()
    if err:
        return err
    session = await BrowserSession.get()
    if not session.is_open():
        return {"error": "Sessão não aberta."}
    tabs = []
    for i, p in enumerate(session.pages):
        try:
            tabs.append({
                "index": i,
                "url": p.url,
                "title": await p.title(),
                "active": i == session.active_idx,
            })
        except Exception:
            continue
    return {"tabs": tabs, "count": len(tabs)}


async def _browser_new_tab(url: str | None = None) -> dict:
    err = _check_available()
    if err:
        return err
    if url:
        url_err = validate_browser_url(url) or _domain_allowed(url)
        if url_err:
            return {"error": url_err, "blocked": True}
    try:
        session = await _ensure_session()
        page = await session.context.new_page()
        if page not in session.pages:
            session.pages.append(page)
        session.active_idx = session.pages.index(page)
        result = {"index": session.active_idx, "tab_count": len(session.pages)}
        if url:
            await page.goto(url, timeout=30000)
            result["url"] = page.url
            result["title"] = await page.title()
        return result
    except Exception as e:
        return {"error": str(e)}


async def _browser_switch_tab(index: int) -> dict:
    err = _check_available()
    if err:
        return err
    session = await BrowserSession.get()
    if not session.is_open():
        return {"error": "Sessão não aberta."}
    if index < 0 or index >= len(session.pages):
        return {"error": f"Índice inválido {index}. Faixa: 0..{len(session.pages) - 1}"}
    session.active_idx = index
    page = session.pages[index]
    try:
        await page.bring_to_front()
        return {"index": index, "url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


async def _browser_close_tab(index: int | None = None) -> dict:
    err = _check_available()
    if err:
        return err
    session = await BrowserSession.get()
    if not session.is_open():
        return {"error": "Sessão não aberta."}
    if index is None:
        index = session.active_idx
    if index < 0 or index >= len(session.pages):
        return {"error": f"Índice inválido {index}"}
    page = session.pages[index]
    try:
        await page.close()
    except Exception:
        pass
    session.pages.pop(index)
    if not session.pages:
        await session.close()
        return {"closed": index, "session_closed": True}
    session.active_idx = max(0, min(session.active_idx, len(session.pages) - 1))
    return {"closed": index, "tab_count": len(session.pages), "active_tab": session.active_idx}


# ─── Interaction (DESTRUCTIVE) ───────────────────────────────────


async def _browser_click(selector: str, timeout: int = 10) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.click(selector, timeout=timeout * 1000)
        return {"selector": selector, "clicked": True, "url": page.url}
    except Exception as e:
        return {"error": str(e)}


async def _browser_fill(selector: str, value: str, timeout: int = 10) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        await page.fill(selector, value, timeout=timeout * 1000)
        return {"selector": selector, "filled": True, "length": len(value)}
    except Exception as e:
        return {"error": str(e)}


async def _browser_select_option(selector: str, value: str, timeout: int = 10) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        result = await page.select_option(selector, value=value, timeout=timeout * 1000)
        return {"selector": selector, "selected": result}
    except Exception as e:
        return {"error": str(e)}


async def _browser_press_key(key: str, selector: str | None = None) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        if selector:
            await page.press(selector, key)
        else:
            await page.keyboard.press(key)
        return {"key": key, "pressed": True}
    except Exception as e:
        return {"error": str(e)}


async def _browser_execute_js(code: str) -> dict:
    page, err = await _require_page()
    if err:
        return err
    try:
        result = await page.evaluate(code)
        try:
            import json

            json.dumps(result)
            return {"result": result}
        except (TypeError, ValueError):
            return {"result": str(result)}
    except Exception as e:
        return {"error": str(e)}


# ─── Tool registrations ──────────────────────────────────────────

_NO_PARAMS = {"type": "object", "properties": {}}


def _reg(name: str, desc: str, params: dict, executor, safety: ToolSafety):
    register_tool(
        ToolDefinition(
            name=name,
            description=desc,
            parameters=params,
            safety=safety,
            category="browser",
            executor=executor,
        )
    )


_reg(
    "browser_open",
    "Abrir uma sessão persistente de navegador (Chromium). Reutiliza sessão existente.",
    {
        "type": "object",
        "properties": {
            "headless": {
                "type": "boolean",
                "description": "Executar sem interface gráfica",
                "default": True,
            }
        },
    },
    _browser_open,
    ToolSafety.SAFE,
)

_reg(
    "browser_close",
    "Fechar a sessão de navegador e liberar recursos.",
    _NO_PARAMS,
    _browser_close,
    ToolSafety.SAFE,
)

_reg(
    "browser_status",
    "Retornar estado atual da sessão (URL, título, abas).",
    _NO_PARAMS,
    _browser_status,
    ToolSafety.SAFE,
)

_reg(
    "browser_navigate",
    "Navegar a aba ativa para uma URL. Aguarda carregamento da página (com JS).",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL completa (http/https)"},
            "wait_until": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle", "commit"],
                "default": "load",
            },
            "timeout": {"type": "integer", "description": "Timeout em segundos", "default": 30},
        },
        "required": ["url"],
    },
    _browser_navigate,
    ToolSafety.SAFE,
)

_reg("browser_back", "Voltar para a página anterior no histórico.", _NO_PARAMS, _browser_back, ToolSafety.SAFE)
_reg("browser_forward", "Avançar para a próxima página no histórico.", _NO_PARAMS, _browser_forward, ToolSafety.SAFE)
_reg("browser_reload", "Recarregar a página atual.", _NO_PARAMS, _browser_reload, ToolSafety.SAFE)

_reg(
    "browser_get_content",
    "Obter conteúdo da página atual (texto renderizado por JS ou HTML completo).",
    {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["text", "html"],
                "default": "text",
                "description": "'text' = texto visível; 'html' = HTML completo",
            }
        },
    },
    _browser_get_content,
    ToolSafety.SAFE,
)

_reg(
    "browser_screenshot",
    "Salvar screenshot PNG da página atual no workspace.",
    {
        "type": "object",
        "properties": {
            "save_to": {
                "type": "string",
                "description": "Caminho do arquivo (relativo ao workspace ou absoluto)",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capturar página inteira (não só viewport)",
                "default": False,
            },
        },
    },
    _browser_screenshot,
    ToolSafety.SAFE,
)

_reg(
    "browser_describe_page",
    "Listar elementos interativos visíveis (links, botões, inputs) com seletores prontos para click/fill.",
    _NO_PARAMS,
    _browser_describe_page,
    ToolSafety.SAFE,
)

_reg(
    "browser_query",
    "Consultar elementos por seletor CSS. Retorna texto, visibilidade e atributo opcional.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "Seletor CSS"},
            "attribute": {
                "type": "string",
                "description": "Atributo HTML para extrair (href, src, value...)",
            },
        },
        "required": ["selector"],
    },
    _browser_query,
    ToolSafety.SAFE,
)

_reg(
    "browser_wait_for",
    "Esperar até que um seletor CSS apareça na página.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "timeout": {"type": "integer", "default": 10, "description": "Timeout em segundos"},
        },
        "required": ["selector"],
    },
    _browser_wait_for,
    ToolSafety.SAFE,
)

_reg("browser_list_tabs", "Listar todas as abas abertas com URL e título.", _NO_PARAMS, _browser_list_tabs, ToolSafety.SAFE)

_reg(
    "browser_new_tab",
    "Abrir uma nova aba (opcionalmente navegando para uma URL).",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL inicial (opcional)"}
        },
    },
    _browser_new_tab,
    ToolSafety.SAFE,
)

_reg(
    "browser_switch_tab",
    "Trocar a aba ativa pelo índice.",
    {
        "type": "object",
        "properties": {"index": {"type": "integer"}},
        "required": ["index"],
    },
    _browser_switch_tab,
    ToolSafety.SAFE,
)

_reg(
    "browser_close_tab",
    "Fechar uma aba pelo índice (ou a ativa se index omitido).",
    {
        "type": "object",
        "properties": {"index": {"type": "integer"}},
    },
    _browser_close_tab,
    ToolSafety.SAFE,
)

_reg(
    "browser_click",
    "Clicar num elemento (seletor CSS). Requer aprovação.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "timeout": {"type": "integer", "default": 10},
        },
        "required": ["selector"],
    },
    _browser_click,
    ToolSafety.DESTRUCTIVE,
)

_reg(
    "browser_fill",
    "Preencher um input/textarea com um valor. Requer aprovação.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "value": {"type": "string"},
            "timeout": {"type": "integer", "default": 10},
        },
        "required": ["selector", "value"],
    },
    _browser_fill,
    ToolSafety.DESTRUCTIVE,
)

_reg(
    "browser_select_option",
    "Selecionar opção em <select> pelo value. Requer aprovação.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "value": {"type": "string"},
            "timeout": {"type": "integer", "default": 10},
        },
        "required": ["selector", "value"],
    },
    _browser_select_option,
    ToolSafety.DESTRUCTIVE,
)

_reg(
    "browser_press_key",
    "Pressionar tecla (Enter, Tab, ArrowDown, etc). Pode focar elemento via selector. Requer aprovação.",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Nome da tecla (Playwright keyboard)"},
            "selector": {"type": "string", "description": "Elemento para focar antes (opcional)"},
        },
        "required": ["key"],
    },
    _browser_press_key,
    ToolSafety.DESTRUCTIVE,
)

_reg(
    "browser_execute_js",
    "Executar código JavaScript arbitrário no contexto da página. SEMPRE requer aprovação — risco alto.",
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Código JS. Use sintaxe de função flecha: '() => document.title'",
            }
        },
        "required": ["code"],
    },
    _browser_execute_js,
    ToolSafety.DESTRUCTIVE,
)
