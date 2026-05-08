"""Alpha Code — Standalone terminal agent."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version


def _resolve_version() -> str:
    # Prefer the co-located pyproject.toml when running from a checkout.
    # Editable installs (`pip install -e .`) freeze the .dist-info version at
    # install time, so reading metadata first would lag behind release-please
    # bumps until the user re-runs pip. The pyproject is the live source.
    try:
        import tomllib
        from pathlib import Path
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.is_file():
            with pyproject.open("rb") as fh:
                v = tomllib.load(fh).get("project", {}).get("version")
                if v:
                    return v
    except Exception:
        pass
    # Installed-as-wheel fallback (no pyproject in the install tree).
    try:
        return _pkg_version("alpha-code")
    except PackageNotFoundError:
        return "0.0.0+dev"


__version__ = _resolve_version()
