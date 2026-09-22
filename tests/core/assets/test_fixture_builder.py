"""The fixture provider's builder, end to end against the in-memory ``FakeStore``.

This is the ``build`` delivery kind's witness for a private-format provider: the same shape
``routes/assets.py``'s ``POST /assets/build`` drives (read the claim off the manifest, compose the
fingerprint and the derived prefix, hand the builder its opaque options), but run directly so the
test does not need a job transport.
"""

from __future__ import annotations

from tests.core.assets.fixture_provider.builder import (
    FixtureAssetBuilder,
    register_fixture_builder,
)
from tests.core.assets.fixture_provider.provider import (
    BUILD_CAPABILITY,
    FakeStore,
    FixtureLinesProvider,
    publish_fixture,
)

from ada.assets.build import (
    build_fingerprint,
    derived_asset_prefix,
    read_glb_provenance,
    validate_build_summary,
)
from ada.assets.builders import BuildRequest, asset_builder, clear_asset_builders

COLLECTION = "fixture-a"


def _build_request(store: FakeStore, revision: str, node: str = "pump-b") -> tuple[BuildRequest, dict, str]:
    """What the route computes before handing a builder its options -- reproduced here so the
    core test drives the real build path without a job transport."""
    provider = FixtureLinesProvider(store.reader())
    manifest = provider.manifest(COLLECTION, node, revision=revision)
    assert manifest.delivery == "build"
    spec = manifest.build
    hierarchy_source = manifest.hierarchy_revision or manifest.revision
    fingerprint = build_fingerprint(
        options=dict(spec.options),
        fingerprint_inputs=spec.fingerprint_inputs,
        node=node,
        hierarchy_source=hierarchy_source,
    )
    derived_prefix = derived_asset_prefix(
        provider=manifest.provider,
        collection=COLLECTION,
        subject=manifest.subject,
        revision=revision,
        node=node,
        fingerprint=fingerprint,
    )
    request = BuildRequest(
        provider=manifest.provider,
        collection=COLLECTION,
        subject=manifest.subject,
        revision=revision,
        node=node,
        fingerprint=fingerprint,
        hierarchy_source=hierarchy_source,
    )
    return request, dict(spec.options), derived_prefix


def test_fixture_builder_produces_a_summary_the_core_validator_accepts():
    store = FakeStore()
    revision = publish_fixture(store)
    request, options, derived_prefix = _build_request(store, revision)

    builder = FixtureAssetBuilder()
    summary = builder.build(options, request=request, storage=store, scope=None, derived_prefix=derived_prefix)

    # Must not raise: the summary restates the request core composed it from, exactly.
    validate_build_summary(
        summary,
        provider=request.provider,
        collection=request.collection,
        subject=request.subject,
        revision=request.revision,
        node=request.node,
        fingerprint=request.fingerprint,
        derived_prefix=derived_prefix,
    )
    assert summary.glb_key == f"{derived_prefix}/model.glb"
    assert summary.glb_key in store.blobs


def test_fixture_builder_glb_carries_the_same_provenance_as_the_summary():
    store = FakeStore()
    revision = publish_fixture(store)
    request, options, derived_prefix = _build_request(store, revision)

    summary = FixtureAssetBuilder().build(
        options, request=request, storage=store, scope=None, derived_prefix=derived_prefix
    )

    glb_bytes = store.blobs[summary.glb_key]
    assert read_glb_provenance(glb_bytes) == summary.provenance.to_dict()


def test_fixture_builder_counts_are_honest():
    """Only what the builder actually measured: it read the source (its record count) and drew
    exactly the one node it was asked for -- nothing it never computed."""
    store = FakeStore()
    revision = publish_fixture(store)
    request, options, derived_prefix = _build_request(store, revision)

    summary = FixtureAssetBuilder().build(
        options, request=request, storage=store, scope=None, derived_prefix=derived_prefix
    )

    assert summary.counts["drawn"] == 1
    # 6 nodes in the fixture's private source (site, unit-1, unit-2, pump-a, pump-b, tank-c).
    assert summary.counts["source_records"] == 6


def test_fixture_builder_refuses_an_unknown_ref():
    store = FakeStore()
    revision = publish_fixture(store)
    request, options, derived_prefix = _build_request(store, revision)
    options = {**options, "ref": "not-a-real-node"}

    try:
        FixtureAssetBuilder().build(options, request=request, storage=store, scope=None, derived_prefix=derived_prefix)
    except ValueError as exc:
        assert "not-a-real-node" in str(exc)
    else:
        raise AssertionError("expected a ValueError for an unknown ref")


def test_register_fixture_builder_resolves_by_capability():
    clear_asset_builders()
    try:
        register_fixture_builder()
        builder = asset_builder(BUILD_CAPABILITY)
        assert isinstance(builder, FixtureAssetBuilder)
        # Re-registering (the same origin, e.g. discovery plus an explicit preload) is a no-op,
        # not a conflict -- the same idempotency `ada.assets.registry` promises for providers.
        register_fixture_builder()
    finally:
        clear_asset_builders()
