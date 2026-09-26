"""Tests that need downloaded game data skip, naming the command that fetches it, when
that data is missing.

Mojang's data (``vendor/vanilla-26.2``) is never committed. A test that reads from a ``vendor/`` folder that does not exist is
reported as skipped, not failed. When the folder does exist, a missing file inside it is
still a real failure.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

HINTS = {"vanilla-26.2": "uv run python scripts/fetch_vanilla.py"}

VANILLA_PRESENT = (VENDOR / "vanilla-26.2" / "registries.json").exists()


def reason(folder: str) -> str:
    return f"needs vendor/{folder}: run `{HINTS.get(folder, 'see vendor/README.md')}`"


needs_vanilla = pytest.mark.skipif(not VANILLA_PRESENT, reason=reason("vanilla-26.2"))


def _missing_vendor_folder(exc: BaseException) -> str | None:
    """The vendor/ folder this error is about, if that whole folder is absent."""
    if not isinstance(exc, FileNotFoundError):
        return None
    from dungeon.vanilla import VanillaDataMissing

    if isinstance(exc, VanillaDataMissing):
        return None if VANILLA_PRESENT else "vanilla-26.2"
    if not exc.filename:
        return None
    try:
        rel = (ROOT / exc.filename).resolve().relative_to(VENDOR.resolve())
    except ValueError:
        return None
    top = rel.parts[0] if rel.parts else None
    return top if top and not (VENDOR / top).exists() else None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.failed and call.excinfo is not None:
        folder = _missing_vendor_folder(call.excinfo.value)
        if folder:
            rep.outcome = "skipped"
            rep.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {reason(folder)}")
