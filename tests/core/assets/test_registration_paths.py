"""A provider must register identically through either path core offers.

Core has two: ``ADA_WORKER_PRELOAD`` (a comma-separated list of dotted modules the worker imports
before subscribing -- a deliberately fatal import-side-effect hook) and the ``ada.plugins``
entry-point ``register()`` that discovery calls. A provider author picks one, and the tab's
answers must not depend on which.

That is not cosmetic: the preload path is how a capability worker built FROM the base image adds
providers core does not ship, while the entry-point path is how an installed distribution does.
The same provider commonly ships both ways, and a difference here would show as a collection that
appears in one deployment and not another.
"""

import importlib
import json

import pytest
from tests.core.assets.fixture_provider.provider import (
    FIXTURE_PROVIDER_ID,
    FakeStore,
    publish_fixture,
    register_fixture_provider,
)

from ada.assets.registry import asset_providers, clear_asset_providers

PRELOAD_MODULE = "tests.core.assets.fixture_provider.preload_hook"


@pytest.fixture
def store():
    s = FakeStore()
    publish_fixture(s)
    yield s
    clear_asset_providers()


def _answers(store: FakeStore) -> str:
    """Everything the tab reads, as one comparable blob."""
    from ada.assets.registry import asset_provider

    provider = asset_provider(FIXTURE_PROVIDER_ID)
    slice_ = provider.hierarchy(None, "fixture-a")
    claim = provider.delivery(None, "fixture-a", "pump-b")
    return json.dumps(
        {
            "providers": asset_providers(),
            "index": provider.index("fixture-a").to_dict(),
            "tree": {"cols": list(slice_.cols), "rows": [list(r) for r in slice_.rows]},
            "delivery": {"capability": claim.capability, "options": dict(claim.options)},
        },
        sort_keys=True,
    )


def test_preload_and_entry_point_registration_agree_byte_for_byte(store, monkeypatch):
    """The acceptance criterion: identical answers whichever path registered the provider."""
    # Path 1 -- ADA_WORKER_PRELOAD: importlib.import_module on a module with a register side effect.
    clear_asset_providers()
    module = importlib.import_module(PRELOAD_MODULE)
    importlib.reload(module)  # the import side effect is the registration
    module.register_with(store)
    via_preload = _answers(store)

    # Path 2 -- ada.plugins entry point: discovery calls the plugin's register().
    clear_asset_providers()
    register_fixture_provider(store)
    via_entry_point = _answers(store)

    assert via_preload == via_entry_point


def test_a_provider_registered_twice_is_not_listed_twice(store):
    """An entry point can legitimately load twice (discovery plus an explicit preload)."""
    clear_asset_providers()
    register_fixture_provider(store)
    register_fixture_provider(store)
    ids = [p["id"] for p in asset_providers()]
    assert ids.count(FIXTURE_PROVIDER_ID) == 1
