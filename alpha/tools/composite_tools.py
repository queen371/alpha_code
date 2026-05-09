"""Composite tools (macros) for ALPHA agent.

Higher-level operations that combine multiple atomic tools into workflows.
These are meta-tools that orchestrate sequences of tool calls.

SECURITY: Each step in a composite tool uses the existing tool security model.

Apos #030 split: este arquivo agora apenas re-exporta os 4 modulos
especializados. Cada sub-modulo registra sua propria tool no import.
"""

# Re-import sub-modules — each registers itself via register_tool() at import time.
from . import _composite_project  # noqa: F401 — side-effect: register_tool
from . import _composite_tests    # noqa: F401
from . import _composite_snr      # noqa: F401
from . import _composite_deploy   # noqa: F401

# Re-export helpers for backward compat (used by other modules)
from ._composite_helpers import _run_tool, _violation  # noqa: F401
