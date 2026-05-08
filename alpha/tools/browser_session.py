"""
Persistent browser session for Alpha Code.

Holds a single Playwright browser instance shared across all browser_* tools
so cookies, login state, and tab history survive between tool calls.
"""

import asyncio
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = BrowserContext = Page = Playwright = None  # type: ignore


_BLOCKED_SCHEMES = frozenset(
    {"file", "chrome", "chrome-extension", "about", "javascript", "data", "view-source"}
)


class BrowserSession:
    """Singleton Playwright session reused across tool calls."""

    _instance: "BrowserSession | None" = None
    # Lock criado lazy. Antes era `_lock = asyncio.Lock()` no escopo da
    # classe (avaliado no module-load), atrelando-se ao primeiro event
    # loop que tocasse o atributo. O CLI roda asyncio.run() por turn —
    # loop novo cada vez — disparando `RuntimeError: attached to a
    # different loop` na 2a turn. Mesmo padrao de alpha/llm.py.
    _lock: "asyncio.Lock | None" = None
    _lock_loop: object | None = None

    def __init__(self):
        self.playwright: "Playwright | None" = None
        self.browser: "Browser | None" = None
        self.context: "BrowserContext | None" = None
        self.pages: list = []
        self.active_idx: int = 0
        self.headless: bool = True

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if cls._lock is None or cls._lock_loop is not loop:
            cls._lock = asyncio.Lock()
            cls._lock_loop = loop
        return cls._lock

    @classmethod
    async def get(cls) -> "BrowserSession":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def page(self):
        if not self.pages:
            return None
        if self.active_idx >= len(self.pages):
            self.active_idx = 0
        return self.pages[self.active_idx]

    def is_open(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    async def open(self, headless: bool = True) -> None:
        async with self._get_lock():
            if self.is_open():
                return
            if not PLAYWRIGHT_AVAILABLE:
                raise RuntimeError(
                    "Playwright not installed. Run: "
                    "pip install playwright && playwright install chromium"
                )
            # Constroi tudo em locais antes de atribuir a self — se launch()
            # ou new_context() falhar, paramos o playwright e a instancia
            # fica num estado limpo. Sem isto, falhas de launch acumulam
            # 1 runtime por tentativa (combina com #054 close-leak).
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch(headless=headless)
                context = await browser.new_context(
                    user_agent="ALPHA-Browser/1.0",
                    viewport={"width": 1280, "height": 800},
                    accept_downloads=False,
                    java_script_enabled=True,
                )
                page = await context.new_page()
            except Exception:
                try:
                    await pw.stop()
                except Exception as cleanup_err:
                    logger.warning(f"playwright stop failed during cleanup: {cleanup_err}")
                raise
            self.headless = headless
            self.playwright = pw
            self.browser = browser
            self.context = context
            self.pages = [page]
            self.active_idx = 0
            self.context.on("page", self._on_new_page)

    def _on_new_page(self, page) -> None:
        if page not in self.pages:
            self.pages.append(page)

    async def close(self) -> None:
        async with self._get_lock():
            # #065: remover listener `_on_new_page` antes de fechar o
            # context. Sem isto, mesmo apos browser.close, o callback
            # mantinha referencia para `self` enquanto Playwright runtime
            # nao GC'ava o context — ciclos abre/fecha empilhavam listeners.
            if self.context is not None:
                try:
                    self.context.remove_listener("page", self._on_new_page)
                except Exception:
                    # Playwright pode levantar se o context ja foi descartado
                    # — nao impede o close.
                    pass
            if self.browser:
                try:
                    await self.browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    logger.warning(f"Error stopping playwright: {e}")
            self.browser = None
            self.context = None
            self.playwright = None
            self.pages = []
            self.active_idx = 0
        # Reset do singleton (#054): sem isto, proximo `BrowserSession.get()`
        # retorna a mesma instancia fechada com listeners stale. Reabrir cria
        # nova instancia limpa.
        BrowserSession._instance = None


def validate_browser_url(url: str) -> str | None:
    """Returns error string if URL is unsafe, None if OK."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "URL inválida"
    scheme = (parsed.scheme or "").lower()
    if scheme in _BLOCKED_SCHEMES:
        return f"Esquema '{scheme}' bloqueado por segurança"
    if scheme not in ("http", "https"):
        return f"Esquema '{scheme}' não permitido (use http ou https)"
    if not parsed.hostname:
        return "URL sem hostname"
    # userinfo (user:pass@host) e usado por phishing/SSRF para enganar o LLM:
    # `https://github.com:fake-token@evil.com` parece github mas resolve evil.
    if parsed.username or parsed.password:
        return "URL com userinfo (user:pass@) não permitida"
    try:
        from ..net_utils import validate_url as _validate

        return _validate(url)
    except Exception:
        return None


async def shutdown_browser() -> None:
    """Cleanup hook called on application shutdown."""
    if BrowserSession._instance is not None and BrowserSession._instance.is_open():
        await BrowserSession._instance.close()
