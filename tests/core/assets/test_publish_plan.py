"""``ada.assets.publish`` -- the pure layer: a provider PLANS, core WRITES.

Every test here drives ``apply_publish_plan``/``stamp_publish`` directly against a hand-built
``PublishPlan``, never through a provider -- the provider-shaped end-to-end proof is
``test_fixture_publish_sequence.py``. Pinning the pure layer separately is what lets a refusal be
attributed to the CONTRACT (this module) rather than to one provider's particular mistake.

The two rules this module exists to make structural rather than habitual (Decision 6's owner gate,
and "manifests written last so a half-written publish is invisible") are exactly what each test
name states.
"""

from __future__ import annotations

import json

import pytest

from ada.assets.keys import asset_key
from ada.assets.manifest import (
    MANIFEST_FILENAME,
    Actor,
    AssetManifest,
    ChangeRecord,
    parse_manifest,
)
from ada.assets.publish import (
    PlannedWrite,
    PublishError,
    PublishPlan,
    apply_publish_plan,
    stamp_publish,
)

COLLECTION = "plant-a"
REVISION = "20260921T143001Z"
CORE_ACTOR = Actor(id="local-dev", display="Local Dev")


def _manifest_key(subject: str, revision: str = REVISION) -> str:
    return asset_key(COLLECTION, subject, revision, MANIFEST_FILENAME)


def _manifest(*, subject: str, revision: str = REVISION, change: ChangeRecord | None = None, **over) -> AssetManifest:
    base = dict(
        provider="fixture-lines",
        collection=COLLECTION,
        subject=subject,
        revision=revision,
        node=subject,
        produced_at="2026-09-21T14:29:00Z",
        published_at="2026-09-21T14:30:01Z",
        delivery="none",
        change=change,
    )
    base.update(over)
    return AssetManifest(**base)


def _manifest_write(
    *, subject: str, revision: str = REVISION, change: ChangeRecord | None = None, **over
) -> PlannedWrite:
    m = _manifest(subject=subject, revision=revision, change=change, **over)
    return PlannedWrite(key=_manifest_key(subject, revision), data=m.to_json())


def _apply(plan: PublishPlan, *, dry_run: bool = False, replace_existing: bool = False, occupied=None, write=None):
    store: dict[str, bytes] = {}
    if write is None:
        write = store.__setitem__
    outcome = apply_publish_plan(
        plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=dry_run,
        replace_existing=replace_existing,
        occupied=occupied,
        write=write,
    )
    return outcome, store


# --------------------------------------------------------------------------------------------
# The owner gate (Decision 6): only core may set published_by / published_via.
# --------------------------------------------------------------------------------------------


def test_a_provider_setting_published_by_is_refused_by_name():
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(_manifest_write(subject="n1", change=ChangeRecord(published_by=Actor(id="not-core"))),),
    )
    with pytest.raises(PublishError, match="published_by"):
        _apply(plan)


def test_a_provider_setting_published_via_is_refused_by_name():
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(_manifest_write(subject="n1", change=ChangeRecord(published_via="service")),),
    )
    with pytest.raises(PublishError, match="published_via"):
        _apply(plan)


def test_core_stamp_keeps_a_relayed_source_actor_action_and_instant_while_adding_published_by_via():
    """The provider's own claim about its SOURCE (never about who called core) survives the stamp
    unchanged; core only ADDS published_by/published_via, it never touches source_actor/action/
    source_instant -- the two trust levels stay on their own fields (Decision 6)."""
    relayed = ChangeRecord(
        source_actor=Actor(id="upstream-tool", application="Vendor Exporter 9"),
        action="modified",
        source_instant="2026-09-18T09:00:00Z",
    )
    manifest = _manifest(subject="n1", change=relayed)
    stamped = stamp_publish(manifest, published_by=CORE_ACTOR, published_via="user")

    assert stamped.change.published_by == CORE_ACTOR
    assert stamped.change.published_via == "user"
    assert stamped.change.source_actor == relayed.source_actor
    assert stamped.change.action == "modified"
    assert stamped.change.source_instant == "2026-09-18T09:00:00Z"

    # Round-trips through JSON identically -- the stamp is not a display-only annotation.
    reparsed = parse_manifest(stamped.to_json())
    assert reparsed.change.published_by == CORE_ACTOR
    assert reparsed.change.source_actor == relayed.source_actor


# --------------------------------------------------------------------------------------------
# Manifests-last ordering (Decision 2a).
# --------------------------------------------------------------------------------------------


def test_an_artefact_written_after_its_own_manifest_is_refused_naming_the_key():
    late_artefact_key = asset_key(COLLECTION, "n1", REVISION, "model.glb")
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(
            _manifest_write(subject="n1"),  # manifest first -- the mistake this refuses
            PlannedWrite(key=late_artefact_key, data=b"glb-bytes"),
        ),
    )
    with pytest.raises(PublishError, match=late_artefact_key):
        _apply(plan)


def test_artefacts_before_their_own_manifest_is_the_accepted_order():
    """The mirror image of the refusal above: the same subject-revision, artefact then manifest,
    is exactly what a publish is supposed to look like and must not raise."""
    artefact_key = asset_key(COLLECTION, "n1", REVISION, "model.glb")
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(
            PlannedWrite(key=artefact_key, data=b"glb-bytes"),
            _manifest_write(subject="n1"),
        ),
    )
    outcome, store = _apply(plan)
    assert outcome.written[-1] == _manifest_key("n1")
    assert store[artefact_key] == b"glb-bytes"


# --------------------------------------------------------------------------------------------
# Occupancy: a revision is immutable unless the caller says replace.
# --------------------------------------------------------------------------------------------


def test_an_occupied_key_is_refused_without_replace_existing():
    key = _manifest_key("n1")
    plan = PublishPlan(
        collection=COLLECTION, revision=REVISION, subjects=("n1",), writes=(_manifest_write(subject="n1"),)
    )
    with pytest.raises(PublishError, match="replace"):
        _apply(plan, occupied={key})


def test_replace_existing_true_allows_and_reports_the_clash():
    key = _manifest_key("n1")
    plan = PublishPlan(
        collection=COLLECTION, revision=REVISION, subjects=("n1",), writes=(_manifest_write(subject="n1"),)
    )
    outcome, store = _apply(plan, occupied={key}, replace_existing=True)
    assert outcome.replaced == (key,)
    assert key in store  # actually rewritten, not merely permitted


# --------------------------------------------------------------------------------------------
# dry_run: plans, never writes.
# --------------------------------------------------------------------------------------------


def test_dry_run_writes_nothing_but_reports_the_same_written_list_and_echoes_dry_run_true():
    artefact_key = asset_key(COLLECTION, "n1", REVISION, "model.glb")
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(PlannedWrite(key=artefact_key, data=b"glb-bytes"), _manifest_write(subject="n1")),
    )

    def _boom(key, data):  # a real publish would call this; a dry run must never reach it
        raise AssertionError(f"dry_run must not write {key}")

    outcome = apply_publish_plan(
        plan,
        published_by=CORE_ACTOR,
        published_via="user",
        dry_run=True,
        replace_existing=False,
        occupied=None,
        write=_boom,
    )
    assert outcome.dry_run is True
    assert outcome.written == (artefact_key, _manifest_key("n1"))
    assert outcome.to_dict()["dry_run"] is True


def test_a_real_publish_with_no_write_callable_is_refused():
    plan = PublishPlan(
        collection=COLLECTION, revision=REVISION, subjects=("n1",), writes=(_manifest_write(subject="n1"),)
    )
    with pytest.raises(PublishError, match="write callable"):
        apply_publish_plan(
            plan,
            published_by=CORE_ACTOR,
            published_via="user",
            dry_run=False,
            replace_existing=False,
            occupied=None,
            write=None,
        )


def test_an_empty_plan_is_refused_as_not_a_publish():
    plan = PublishPlan(collection=COLLECTION, revision=REVISION, subjects=(), writes=())
    with pytest.raises(PublishError, match="no writes"):
        _apply(plan)


# --------------------------------------------------------------------------------------------
# A manifest core cannot parse.
# --------------------------------------------------------------------------------------------


def test_a_manifest_core_cannot_parse_is_refused_naming_the_key():
    bad_key = _manifest_key("n1")
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(PlannedWrite(key=bad_key, data=b"not json at all"),),
    )
    with pytest.raises(PublishError, match=bad_key):
        _apply(plan)


# --------------------------------------------------------------------------------------------
# Provider artefacts pass through untouched -- core only ever rewrites asset.json.
# --------------------------------------------------------------------------------------------


def test_provider_artefacts_pass_through_byte_identical():
    artefact_key = asset_key(COLLECTION, "n1", REVISION, "model.glb")
    opaque_bytes = b"\x00glTF-not-really-but-opaque-to-core\xff"
    plan = PublishPlan(
        collection=COLLECTION,
        revision=REVISION,
        subjects=("n1",),
        writes=(PlannedWrite(key=artefact_key, data=opaque_bytes), _manifest_write(subject="n1")),
    )
    outcome, store = _apply(plan)
    assert store[artefact_key] == opaque_bytes  # untouched, not even re-encoded
    # The manifest, by contrast, WAS rewritten -- it now carries core's stamp.
    written_manifest = json.loads(store[_manifest_key("n1")])
    assert written_manifest["change"]["published_by"]["id"] == "local-dev"
