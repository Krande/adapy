"""A worker can advertise an engine it has, with no database row.

Before this, a procedural engine existed for the viewer only as a row an admin
created per scope. Installing the engine's package in a worker was necessary but
not sufficient — the engine simply did not appear, and there was nothing in the
running system to tell you why.

The registry now carries a full descriptor, so a worker announcing it in its
heartbeat is enough for the viewer to offer the engine and route to it. This is
the shape detailing engines already use.

The distinction these tests pin is *offerability*: a spec carrying capability
flags alone describes an engine the viewer already knows about, while a spec
carrying a name and an entrypoint describes one it can offer on its own. Getting
that wrong in either direction is bad — surfacing a flags-only spec offers an
engine nothing can dispatch to, and ignoring a full spec puts us back to manual
registration.
"""

from __future__ import annotations

import pytest

from ada.topo_model.engine_catalog import (
    is_offerable,
    procedural_engine_specs,
    register_procedural_engine_capabilities,
)


@pytest.fixture()
def clean_slug():
    """Register under a slug no built-in uses, and leave the registry as found.

    The registry is module-level and pre-seeded with the built-ins, so a test
    that registered without cleaning up would leak into every later assertion.
    """
    slug = "test-engine-xyz"
    yield slug
    from ada.topo_model.engine_catalog import _ENGINE_CAPABILITY_REGISTRY

    _ENGINE_CAPABILITY_REGISTRY.pop(slug, None)


def _spec_for(slug: str) -> dict | None:
    return next((s for s in procedural_engine_specs() if s["slug"] == slug), None)


def test_flags_only_registration_is_not_offerable(clean_slug):
    # The original call shape. It says "this engine supports grouping", not
    # "this engine exists" — offering it would surface an engine with no
    # entrypoint for the viewer to dispatch to.
    register_procedural_engine_capabilities(clean_slug, supports_grouping=True)
    spec = _spec_for(clean_slug)
    assert spec is not None
    assert spec["supports_grouping"] is True
    assert not is_offerable(spec)


def test_full_descriptor_is_offerable(clean_slug):
    register_procedural_engine_capabilities(
        clean_slug,
        supports_grouping=True,
        name="Test Engine",
        description="An engine used by the tests.",
        entrypoint="some_package.engine:compile",
        worker_capability="test-pool",
    )
    spec = _spec_for(clean_slug)
    assert is_offerable(spec)
    assert spec["name"] == "Test Engine"
    assert spec["entrypoint"] == "some_package.engine:compile"
    assert spec["worker_capability"] == "test-pool"


def test_a_name_without_an_entrypoint_is_not_offerable(clean_slug):
    # Half a descriptor is the dangerous case: enough to look like an engine in
    # a list, not enough to run. It must fail closed.
    register_procedural_engine_capabilities(clean_slug, name="Test Engine")
    assert not is_offerable(_spec_for(clean_slug))


def test_an_entrypoint_without_a_name_is_not_offerable(clean_slug):
    register_procedural_engine_capabilities(clean_slug, entrypoint="some_package.engine:compile")
    assert not is_offerable(_spec_for(clean_slug))


def test_worker_capability_is_optional(clean_slug):
    # An engine any worker can run advertises no pool; routing then leaves
    # target_capability unset, which is the default pool.
    register_procedural_engine_capabilities(clean_slug, name="Test Engine", entrypoint="some_package.engine:compile")
    spec = _spec_for(clean_slug)
    assert is_offerable(spec)
    assert "worker_capability" not in spec


def test_registration_is_idempotent_and_replaces(clean_slug):
    register_procedural_engine_capabilities(clean_slug, name="First", entrypoint="some_package.engine:compile")
    register_procedural_engine_capabilities(clean_slug, name="Second", entrypoint="other_package.engine:compile")
    specs = [s for s in procedural_engine_specs() if s["slug"] == clean_slug]
    assert len(specs) == 1, "re-registering a slug must replace, not duplicate"
    assert specs[0]["name"] == "Second"


def test_builtins_remain_flags_only():
    # The built-ins are listed statically by the endpoint and must NOT be
    # re-offered as worker-advertised engines, or they would appear twice.
    from ada.topo_model.engines import BUILTIN_ENGINES

    for slug in BUILTIN_ENGINES:
        spec = _spec_for(slug)
        assert spec is not None, f"built-in {slug} should still announce its flags"
        assert not is_offerable(spec), f"built-in {slug} must not be offerable as a worker engine"
