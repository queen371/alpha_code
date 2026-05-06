"""Generic file-backed registry shared by agents/skills/etc.

Antes existia copia identica em `alpha/agents/registry.py` e
`alpha/skills/registry.py` — qualquer mudanca em uma precisava ser
duplicada na outra (#DM008 DEEP_MAINTAINABILITY).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


class FileBackedRegistry(Generic[T]):
    """Discover and cache typed entries from glob patterns under search paths.

    Each entry T precisa ter um atributo `name` (lido pelo registry para
    indexar). O loader recebe um Path e retorna T (ou levanta excecao
    para descartar a entrada).
    """

    def __init__(
        self,
        search_paths: list[Path],
        glob_pattern: str,
        loader: Callable[[Path], T],
        kind: str = "entry",
    ):
        self._search_paths = search_paths
        self._glob_pattern = glob_pattern
        self._loader = loader
        self._kind = kind
        self._registry: dict[str, T] = {}
        self._loaded = False

    def load_all(self, force: bool = False) -> dict[str, T]:
        """Scan and populate. Idempotent unless `force=True`."""
        if self._loaded and not force:
            return self._registry
        if force:
            self._registry.clear()

        for base in self._search_paths:
            if not base.is_dir():
                continue
            for path in sorted(base.glob(self._glob_pattern)):
                try:
                    entry = self._loader(path)
                    self._registry[entry.name] = entry
                except Exception as e:
                    logger.warning(f"Failed to load {self._kind} {path}: {e}")

        self._loaded = True
        logger.info(f"{self._kind.capitalize()}s loaded: {len(self._registry)}")
        return self._registry

    def get(self, name: str) -> T | None:
        if not self._loaded:
            self.load_all()
        return self._registry.get(name)

    def list(self) -> list[T]:
        if not self._loaded:
            self.load_all()
        return sorted(self._registry.values(), key=lambda e: e.name)

    @property
    def loaded(self) -> bool:
        return self._loaded
