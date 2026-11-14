import asyncio

import pytest

from . import fakeha


@pytest.fixture
def run():
    """Run a coroutine to the end (no pytest-asyncio here)."""
    return lambda coro: asyncio.run(coro)


@pytest.fixture
def ha(monkeypatch):
    """-> (the component package, a fake hass) with the fake Home Assistant in sys.modules for this test."""
    return fakeha.load(monkeypatch)


from .realseat import real_seat, release_seat  # noqa: E402,F401  (the fixtures)
