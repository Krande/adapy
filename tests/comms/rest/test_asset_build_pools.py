"""A worker advertises an asset-build capability only where the builder can actually run.

The two ways to get this wrong are both silent, which is why it is derived rather than
configured: a pool that advertises a builder it cannot import takes the job and times out, and a
pool that can serve one without advertising it leaves the job queued for ever.
"""

import pytest

from ada.assets.builders import (
    clear_asset_builders,
    module_available,
    register_asset_builder,
)
from ada.comms.rest.worker.pools import _pool_capabilities


class _Builder:
    def build(self, options, **kwargs):  # pragma: no cover - never invoked here
        raise AssertionError("this test only asks which capabilities are advertised")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_asset_builders()
    yield
    clear_asset_builders()


def test_an_available_builder_adds_its_capability_to_the_pools():
    register_asset_builder("asset-build-probe", _Builder)
    pools = _pool_capabilities(["base"])
    assert pools[0] == "base"
    assert "asset-build-probe" in pools
    # Membership, not equality: core ships its own builders (the IFC one), and whether THEY are
    # available here is a property of the environment, not of this rule.


def test_an_unavailable_builder_is_not_advertised():
    register_asset_builder(
        "asset-build-absent",
        _Builder,
        available=module_available("a_module_that_is_not_installed_anywhere"),
    )
    assert "asset-build-absent" not in _pool_capabilities(["base"])


def test_a_probe_that_raises_counts_as_unavailable():
    def _angry() -> bool:
        raise RuntimeError("probe blew up")

    register_asset_builder("asset-build-angry", _Builder, available=_angry)
    assert "asset-build-angry" not in _pool_capabilities(["base"])


def test_a_worker_disabled_down_to_nothing_does_not_acquire_build_work():
    """Subtraction has already had its say by the time pools are composed: a worker left with no
    capabilities must not re-acquire work through the builder registry."""
    register_asset_builder("asset-build-probe", _Builder)
    assert _pool_capabilities([]) == ["base"]


def test_capabilities_are_normalised_and_deduplicated():
    register_asset_builder("asset-build-probe", _Builder)
    pools = _pool_capabilities(["base", "Base", "asset-build-probe"])
    assert pools.count("base") == 1
    assert pools.count("asset-build-probe") == 1


def test_only_a_worker_serving_base_acquires_build_capabilities():
    """A worker declared for ONE capability was pointed at one kind of work. Acquiring asset
    builds because its image happens to import a builder is the surprise
    ADA_WORKER_BASE_CONVERSIONS exists to prevent; a dedicated build pool is declared, not
    inferred."""
    register_asset_builder("asset-build-probe", _Builder)
    assert _pool_capabilities(["weld-gen"]) == ["weld-gen"]
    assert _pool_capabilities(["weld-gen", "asset-build-probe"]) == ["weld-gen", "asset-build-probe"]
