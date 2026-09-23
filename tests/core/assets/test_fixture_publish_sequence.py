"""End to end: publish -> leaf-publish -> unpublish -> orphan, driven by the private ``fixture-lines``
format against the in-memory ``FakeStore``.

This is the STRONGER witness §Verification asks for (Decision 7's semantics-not-format gate): every
manifest this sequence writes -- through the whole-tree publish AND the leaf-without-stem publish --
carries no ``change.source_actor``/``action``, no ``attributes`` artefact, and the only ``change``
field ever present is what CORE adds (``published_by``/``published_via``). The mechanics (a shared
source blob referenced by ``key``, the refcount-checked unpublish, a leaf revision the collection
index does not know about) are proven with a source format core has no reader for -- the point of
the fixture provider existing at all (Decision 1's "test that pins it").

The leaf-without-stem publish (``_leaf_publish_plan``) is hand-built rather than routed through
``FixtureLinesPublisher.derive()`` -- that method always derives the WHOLE private-format tree (it
has no ``--leaf`` scope of its own, unlike the IFC provider). Building the plan directly with the
same public contract (``PublishPlan``/``PlannedWrite``/``AssetManifest``) still proves what this
suite needs: that ``apply_publish_plan``/``plan_unpublish`` support leaf-scoped publishing for ANY
provider, not just the IFC one, and that a private-format manifest survives the whole trip with no
adopted IFC carrier ever set.
"""

from __future__ import annotations

import json

from tests.core.assets.fixture_provider.provider import FakeStore, FixtureLinesProvider
from tests.core.assets.fixture_provider.publisher import (
    FixtureLinesPublisher,
    stage_fixture_source,
)

from ada.assets.index import fold_listing
from ada.assets.keys import ASSET_PREFIX, asset_key, revision_from_instant
from ada.assets.manifest import MANIFEST_FILENAME, Actor, ArtefactEntry, AssetManifest
from ada.assets.publish import PlannedWrite, PublishPlan, apply_publish_plan
from ada.assets.unpublish import plan_unpublish

COLLECTION = "fixture-seq"
CORE_ACTOR = Actor(id="local-dev", display="Local Dev")
LEAF_INSTANT = "2026-09-22T09:00:00Z"  # after DEFAULT_INSTANT: a later, leaf-only publish


def _publish_v1(store: FakeStore) -> tuple[PublishPlan, dict]:
    stage_fixture_source(store, staging_id="up1")
    staged = {"source.jsonl": f"{ASSET_PREFIX}/_staging/up1/source.jsonl"}
    plan = FixtureLinesPublisher().derive(None, staged, storage=store, collection=COLLECTION, options={}, dry_run=False)
    outcome = apply_publish_plan(
        plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(store.list_prefix(f"{ASSET_PREFIX}/{COLLECTION}/")),
        write=store.put,
    )
    return plan, outcome.to_dict()


def _leaf_publish_plan(*, source_key: str, subject: str, instant: str) -> PublishPlan:
    """A leaf-without-stem publish, by hand: one manifest, no fresh source upload -- it names the
    ALREADY-published collection source blob by absolute ``key``, exactly the shape
    ``ada.assets.unpublish``'s refcount check exists for (Decision 3)."""
    revision = revision_from_instant(instant)
    manifest = AssetManifest(
        provider="fixture-lines",
        collection=COLLECTION,
        subject=subject,
        revision=revision,
        node=subject,
        produced_at=instant,
        published_at=instant,
        delivery="none",
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="reused", size=0),),
        counts={"nodes": 1},
        # No change=: a leaf-only publish through this format relays nothing either.
    )
    write = PlannedWrite(asset_key(COLLECTION, subject, revision, MANIFEST_FILENAME), manifest.to_json())
    return PublishPlan(collection=COLLECTION, revision=revision, subjects=(subject,), writes=(write,))


def _all_manifests(store: FakeStore) -> dict[str, dict]:
    out = {}
    for key in store.list_prefix(f"{ASSET_PREFIX}/{COLLECTION}/"):
        if key.rsplit("/", 1)[-1] == MANIFEST_FILENAME:
            out[key] = json.loads(store.blobs[key])
    return out


# --------------------------------------------------------------------------------------------
# Publish: the semantics-not-format witness.
# --------------------------------------------------------------------------------------------


def test_publish_writes_manifests_with_change_attributes_and_action_all_absent():
    store = FakeStore()
    _plan, outcome = _publish_v1(store)
    assert outcome["dry_run"] is False
    assert len(outcome["subjects"]) == 6  # site, unit-1, unit-2, pump-a, pump-b, tank-c

    manifests = _all_manifests(store)
    assert len(manifests) == 7  # 6 nodes + the collection-level manifest
    for key, raw in manifests.items():
        # The ONLY `change` field the fixture format's own publish ever produces is what CORE
        # stamped -- published_by/published_via. Nothing the provider relays (source_actor,
        # action, source_instant) is ever present, because this format's derive() never sets them.
        change = raw.get("change")
        assert change is not None, f"{key}: core must stamp published_by even for a silent provider"
        assert set(change) == {"published_by", "published_via"}, f"{key}: unexpected change field {change}"
        assert change["published_by"]["id"] == "local-dev"
        assert change["published_via"] == "user"
        # No adopted-carrier artefact role this format never produces.
        assert all(a["role"] != "attributes" for a in raw["artefacts"])
        # `action` only ever lives inside `change`; confirm it never appears at all.
        assert "action" not in change


def test_publish_writes_the_collection_manifest_last():
    store = FakeStore()
    _plan, outcome = _publish_v1(store)
    collection_manifest_key = asset_key(COLLECTION, COLLECTION, outcome["revision"], MANIFEST_FILENAME)
    assert outcome["written"][-1] == collection_manifest_key


# --------------------------------------------------------------------------------------------
# Leaf-publish: a second, independent revision that shares v1's source blob by key.
# --------------------------------------------------------------------------------------------


def test_leaf_publish_shares_the_whole_trees_source_blob_and_needs_no_upload_of_its_own():
    store = FakeStore()
    _plan, outcome_v1 = _publish_v1(store)
    source_key = asset_key(COLLECTION, COLLECTION, outcome_v1["revision"], "source.jsonl")
    assert source_key in store.blobs  # sanity: v1 really did upload it

    leaf_plan = _leaf_publish_plan(source_key=source_key, subject="pump-b", instant=LEAF_INSTANT)
    leaf_outcome = apply_publish_plan(
        leaf_plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(),
        write=store.put,
    )

    leaf_key = asset_key(COLLECTION, "pump-b", leaf_plan.revision, MANIFEST_FILENAME)
    assert leaf_outcome.written == (leaf_key,)  # ONE write: no fresh source blob for a leaf publish
    written_manifest = json.loads(store.blobs[leaf_key])
    assert written_manifest["artefacts"][0]["key"] == source_key
    assert "file" not in written_manifest["artefacts"][0]  # shared by key, not copied by filename
    assert "change" in written_manifest and set(written_manifest["change"]) == {"published_by", "published_via"}


def test_leaf_publish_is_what_the_index_resolves_as_pump_bs_latest_revision():
    """The mechanism an "ahead" orphan (Decision 3) is decided from: a subject with a NEWER
    manifest the collection-level spine was never re-derived against still resolves as latest."""
    store = FakeStore()
    _plan, outcome_v1 = _publish_v1(store)
    source_key = asset_key(COLLECTION, COLLECTION, outcome_v1["revision"], "source.jsonl")
    leaf_plan = _leaf_publish_plan(source_key=source_key, subject="pump-b", instant=LEAF_INSTANT)
    apply_publish_plan(
        leaf_plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(),
        write=store.put,
    )

    idx = fold_listing(store.list_prefix(f"{ASSET_PREFIX}/{COLLECTION}/"))
    pump_b = idx.subject(COLLECTION, "pump-b")
    assert [r.revision for r in pump_b.revisions] == [leaf_plan.revision, outcome_v1["revision"]]  # newest first

    provider = FixtureLinesProvider(store.reader())
    resolved = provider.manifest(COLLECTION, "pump-b")  # no revision= -> "latest"
    assert resolved.revision == leaf_plan.revision
    assert resolved.change is None or resolved.change.action is None  # still nothing "action"-shaped


# --------------------------------------------------------------------------------------------
# Unpublish: refused while ANY surviving manifest references the shared source, across revisions.
# --------------------------------------------------------------------------------------------


def _v1_node_subjects() -> tuple[str, ...]:
    return ("site", "unit-1", "unit-2", "pump-a", "pump-b", "tank-c")


def _unpublish(store: FakeStore, *, subject: str, revision: str):
    keys = list(store.list_prefix(f"{ASSET_PREFIX}/{COLLECTION}/"))
    manifest_bytes = {
        k: store.blobs[k]
        for k in keys
        if k.rsplit("/", 1)[-1] == MANIFEST_FILENAME
        and not k.startswith(f"{ASSET_PREFIX}/{COLLECTION}/{subject}/{revision}/")
    }
    plan = plan_unpublish(
        collection=COLLECTION, subject=subject, revision=revision, collection_keys=keys, manifest_bytes=manifest_bytes
    )
    if not plan.refused:
        for key in plan.deleted:
            del store.blobs[key]
    return plan


def test_unpublish_of_the_collection_revision_is_refused_while_v1_node_manifests_still_reference_it():
    store = FakeStore()
    _plan, outcome_v1 = _publish_v1(store)

    plan = _unpublish(store, subject=COLLECTION, revision=outcome_v1["revision"])
    assert plan.refused
    assert plan.held_by  # named holders, not a bare refusal
    for holder in plan.held_by:
        assert holder.endswith(f"@{outcome_v1['revision']}")


def test_unpublish_of_the_collection_revision_still_refused_after_v1_siblings_go_while_the_leaf_publish_survives():
    """The refcount check spans REVISIONS, not just same-revision siblings: a leaf published later
    against the same shared blob keeps the collection revision alive even once every v1 sibling
    that originally referenced it is gone."""
    store = FakeStore()
    _plan, outcome_v1 = _publish_v1(store)
    source_key = asset_key(COLLECTION, COLLECTION, outcome_v1["revision"], "source.jsonl")
    leaf_plan = _leaf_publish_plan(source_key=source_key, subject="pump-b", instant=LEAF_INSTANT)
    apply_publish_plan(
        leaf_plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(),
        write=store.put,
    )

    for subject in _v1_node_subjects():
        result = _unpublish(store, subject=subject, revision=outcome_v1["revision"])
        assert not result.refused, f"{subject}@v1 should unpublish cleanly: {result.reason}"

    # Every v1 sibling is gone, but the leaf publish at LEAF_INSTANT still names the same key.
    plan = _unpublish(store, subject=COLLECTION, revision=outcome_v1["revision"])
    assert plan.refused
    assert any(h.startswith("pump-b@") for h in plan.held_by)


def test_unpublish_of_the_collection_revision_succeeds_once_the_leaf_publish_is_also_gone():
    store = FakeStore()
    _plan, outcome_v1 = _publish_v1(store)
    source_key = asset_key(COLLECTION, COLLECTION, outcome_v1["revision"], "source.jsonl")
    leaf_plan = _leaf_publish_plan(source_key=source_key, subject="pump-b", instant=LEAF_INSTANT)
    apply_publish_plan(
        leaf_plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(),
        write=store.put,
    )
    for subject in _v1_node_subjects():
        assert not _unpublish(store, subject=subject, revision=outcome_v1["revision"]).refused
    assert not _unpublish(store, subject="pump-b", revision=leaf_plan.revision).refused

    plan = _unpublish(store, subject=COLLECTION, revision=outcome_v1["revision"])
    assert not plan.refused
    assert plan.deleted[0] == asset_key(COLLECTION, COLLECTION, outcome_v1["revision"], MANIFEST_FILENAME)
    assert source_key in plan.deleted
    assert source_key not in store.blobs  # actually gone, not merely planned

    # Nothing under this collection was ever asked to carry change/attributes/action beyond core's
    # own stamp -- the property the whole sequence exists to pin, checked one last time at the end.
    for raw in _all_manifests(store).values():  # only pump-b's own manifest, if anything, survives
        assert all(a["role"] != "attributes" for a in raw["artefacts"])
