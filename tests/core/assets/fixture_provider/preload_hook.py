"""An ADA_WORKER_PRELOAD-shaped module: importing it is what registers the provider.

The worker's preload hook is ``importlib.import_module`` over a dotted path, so a provider that
ships this way does its registration as an import side effect. This module keeps the store
injectable for tests via ``register_with`` while presenting that same shape.
"""

from __future__ import annotations

from tests.core.assets.fixture_provider.provider import register_fixture_provider

_PENDING: list = []


def register_with(store) -> None:
    """What a real preload module does at import time, with the store passed in."""
    register_fixture_provider(store)
    _PENDING.append(store)
