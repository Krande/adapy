"""``IfcAssetBuilder`` end to end: a member build draws exactly one product, the GLB's own
``asset.extras.provenance`` matches the summary's, and ``validate_build_summary`` refuses a
summary answering a different request. Also the registry integration (``ensure_core_builders``
reaches ``asset-build-ifc`` without importing ifcopenshell/adacpp at package-import time).
"""

from __future__ import annotations

import dataclasses
import pathlib

import ifcopenshell
import pytest

from ada.assets.build import (
    BuildError,
    BuildProvenance,
    build_fingerprint,
    derived_asset_prefix,
    read_glb_provenance,
    validate_build_summary,
)
from ada.assets.builders import (
    BuildRequest,
    asset_builder,
    available_build_capabilities,
)
from ada.assets.ifc.build import IfcAssetBuilder
from ada.assets.ifc.publish import publish_ifc
from ada.assets.published import PublishedAssetProvider

from .fake_store import FakeStore, FakeSyncStorageFacade

CORPUS = pathlib.Path(__file__).parents[1] / "corpus" / "plant-a_v1.ifc"
STAGED_KEY = "assets/_staging/x/source.ifc"


def _raw() -> bytes:
    return CORPUS.read_bytes()


def _member_guid(name: str) -> str:
    f = ifcopenshell.file.from_string(_raw().decode())
    return next(p.GlobalId for p in f.by_type("IfcProduct") if p.Name == name)


@pytest.fixture
def published_member():
    store = FakeStore()
    store.put_bytes(STAGED_KEY, _raw())
    beam_guid = _member_guid("a1-bm0")
    publish_ifc(store, collection="plant-a", staged_key=STAGED_KEY, leaf=beam_guid, extracted_at="2026-01-01T00:00:00Z")
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    claim = provider.delivery(None, "plant-a", beam_guid)
    return store, beam_guid, claim


def _request_for(claim, node: str) -> tuple[BuildRequest, str, str]:
    fingerprint = build_fingerprint(
        options=claim.options,
        fingerprint_inputs=claim.fingerprint_inputs,
        node=node,
        hierarchy_source="published-spine",
    )
    prefix = derived_asset_prefix(
        provider="ifc", collection="plant-a", subject=node, revision=claim.revision, node=node, fingerprint=fingerprint
    )
    request = BuildRequest(
        provider="ifc",
        collection="plant-a",
        subject=node,
        revision=claim.revision,
        node=node,
        fingerprint=fingerprint,
        hierarchy_source="published-spine",
    )
    return request, fingerprint, prefix


def test_member_build_draws_exactly_one_and_provenance_matches(published_member):
    store, beam_guid, claim = published_member
    request, fingerprint, prefix = _request_for(claim, beam_guid)

    builder = IfcAssetBuilder()
    summary = builder.build(
        claim.options, request=request, storage=FakeSyncStorageFacade(store), scope=None, derived_prefix=prefix
    )

    assert summary.ok is True
    assert summary.counts == {"drawn": 1}
    assert summary.glb_key.startswith(prefix + "/")

    validate_build_summary(
        summary,
        provider="ifc",
        collection="plant-a",
        subject=beam_guid,
        revision=claim.revision,
        node=beam_guid,
        fingerprint=fingerprint,
        derived_prefix=prefix,
    )

    glb_bytes = store.get_bytes(summary.glb_key)
    assert glb_bytes[:4] == b"glTF"
    assert read_glb_provenance(glb_bytes) == summary.provenance.to_dict()


def test_a_summary_whose_revision_disagrees_is_refused(published_member):
    store, beam_guid, claim = published_member
    request, fingerprint, prefix = _request_for(claim, beam_guid)
    builder = IfcAssetBuilder()
    summary = builder.build(
        claim.options, request=request, storage=FakeSyncStorageFacade(store), scope=None, derived_prefix=prefix
    )

    wrong = dataclasses.replace(
        summary, provenance=dataclasses.replace(summary.provenance, revision="19990101T000000Z")
    )
    with pytest.raises(BuildError, match="provenance.revision"):
        validate_build_summary(
            wrong,
            provider="ifc",
            collection="plant-a",
            subject=beam_guid,
            revision=claim.revision,
            node=beam_guid,
            fingerprint=fingerprint,
            derived_prefix=prefix,
        )


def test_refuses_rather_than_widens_when_the_spine_is_missing(published_member):
    """The builder must not fall back to converting the whole file when its own published spine
    key is absent -- that would draw geometry the browser never showed as belonging to this node."""
    store, beam_guid, claim = published_member
    request, _fingerprint, prefix = _request_for(claim, beam_guid)
    bad_options = dict(claim.options)
    bad_options["hierarchy_key"] = "assets/plant-a/does-not-exist/00000000T000000Z/hierarchy.json"

    builder = IfcAssetBuilder()
    with pytest.raises(BuildError, match="published spine"):
        builder.build(
            bad_options, request=request, storage=FakeSyncStorageFacade(store), scope=None, derived_prefix=prefix
        )


def test_provenance_carries_a_real_source_hash(published_member):
    store, beam_guid, claim = published_member
    request, _fingerprint, prefix = _request_for(claim, beam_guid)
    builder = IfcAssetBuilder()
    summary = builder.build(
        claim.options, request=request, storage=FakeSyncStorageFacade(store), scope=None, derived_prefix=prefix
    )
    assert isinstance(summary.provenance, BuildProvenance)
    (source,) = summary.provenance.sources
    assert source["key"] == claim.options["source_key"]
    assert len(source["sha256"]) == 64


# --- registry integration -------------------------------------------------------------------------


def test_asset_build_ifc_is_registered_and_available():
    # NOT `clear_asset_builders()` then re-check: registration is an IMPORT-TIME side effect of
    # `ada.assets.ifc`'s module body, and `ensure_core_builders()` re-imports it via
    # `importlib.import_module`, which does not re-execute an already-cached module. Clearing here
    # would therefore prove nothing about this test process, only about whether some earlier test
    # happened to run first -- so this checks the steady state instead.
    assert "asset-build-ifc" in available_build_capabilities()
    builder = asset_builder("asset-build-ifc")
    assert isinstance(builder, IfcAssetBuilder)
