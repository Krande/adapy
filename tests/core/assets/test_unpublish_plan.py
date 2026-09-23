"""``ada.assets.unpublish`` -- the refcount check core owes its publishers.

``plan_unpublish`` is pure (Decision 3, "deletion / orphan semantics"): the route does the listing
and the manifest reads, this module only decides. Every test builds ``collection_keys`` and
``manifest_bytes`` by hand -- no store, no provider -- so a failure here is unambiguously the
CONTRACT's, never a provider's construction of it (that end-to-end proof is
``test_fixture_publish_sequence.py``).
"""

from __future__ import annotations

from ada.assets.keys import asset_key
from ada.assets.manifest import MANIFEST_FILENAME, ArtefactEntry, AssetManifest
from ada.assets.unpublish import plan_unpublish

COLLECTION = "plant-a"
STOREY_SUBJECT = "storey-1"
STOREY_REVISION = "20260921T143001Z"  # v1: the revision that ORIGINALLY uploaded the shared source
LEAF_SUBJECT = "member-42"
LEAF_REVISION = "20260922T090000Z"  # v2: a later, leaf-only publish that reuses v1's source blob


def _key(subject: str, revision: str, filename: str) -> str:
    return asset_key(COLLECTION, subject, revision, filename)


def _manifest_bytes(
    *, subject: str, revision: str, node: str | None = None, artefacts: tuple[ArtefactEntry, ...] = ()
) -> bytes:
    return AssetManifest(
        provider="ifc",
        collection=COLLECTION,
        subject=subject,
        revision=revision,
        node=node if node is not None else subject,
        produced_at="2026-09-21T14:29:00Z",
        published_at="2026-09-21T14:30:01Z",
        delivery="none",
        artefacts=artefacts,
    ).to_json()


def _storey_keys() -> list[str]:
    """The storey's own revision: a manifest, a hierarchy, and the ACTUAL uploaded source blob --
    the "leaf without stem" shape (Decision 3), where a later leaf publish points at this source by
    absolute `key` instead of uploading its own copy."""
    return [
        _key(STOREY_SUBJECT, STOREY_REVISION, MANIFEST_FILENAME),
        _key(STOREY_SUBJECT, STOREY_REVISION, "hierarchy.json"),
        _key(STOREY_SUBJECT, STOREY_REVISION, "source.ifc"),
    ]


def _storey_source_key() -> str:
    return _key(STOREY_SUBJECT, STOREY_REVISION, "source.ifc")


def _leaf_manifest_bytes(source_key: str) -> bytes:
    """A leaf-without-stem manifest: it names the storey's source blob by absolute KEY, which is
    the one thing that makes deleting the storey's revision unsafe while this survives."""
    return _manifest_bytes(
        subject=LEAF_SUBJECT,
        revision=LEAF_REVISION,
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="s" * 64, size=100),),
    )


# --------------------------------------------------------------------------------------------
# The refcount rule itself.
# --------------------------------------------------------------------------------------------


def test_a_surviving_leaf_manifest_naming_the_shared_source_by_key_blocks_the_storey_delete():
    source_key = _storey_source_key()
    leaf_manifest = _leaf_manifest_bytes(source_key)
    collection_keys = _storey_keys() + [_key(LEAF_SUBJECT, LEAF_REVISION, MANIFEST_FILENAME)]

    plan = plan_unpublish(
        collection=COLLECTION,
        subject=STOREY_SUBJECT,
        revision=STOREY_REVISION,
        collection_keys=collection_keys,
        # SURVIVING manifests only -- the storey's own manifest is excluded, as the docstring
        # requires: the set being removed cannot hold itself alive.
        manifest_bytes={_key(LEAF_SUBJECT, LEAF_REVISION, MANIFEST_FILENAME): leaf_manifest},
    )

    assert plan.refused
    assert plan.deleted == ()
    assert plan.kept  # the storey's keys are named as KEPT, not silently dropped
    holder = f"{LEAF_SUBJECT}@{LEAF_REVISION}"
    assert holder in plan.held_by
    assert holder in plan.reason


def test_the_storey_delete_succeeds_once_the_leaf_manifest_is_gone():
    collection_keys = _storey_keys()  # the leaf has already been unpublished -- nothing else here

    plan = plan_unpublish(
        collection=COLLECTION,
        subject=STOREY_SUBJECT,
        revision=STOREY_REVISION,
        collection_keys=collection_keys,
        manifest_bytes={},  # no surviving manifest references the storey's blobs any more
    )

    assert not plan.refused
    assert set(plan.deleted) == set(_storey_keys())
    assert plan.kept == ()
    assert plan.held_by == ()


# --------------------------------------------------------------------------------------------
# Delete order: manifest first, so a partial delete reads as unpublished, never as
# published-and-incomplete (the mirror image of "manifests written last").
# --------------------------------------------------------------------------------------------


def test_delete_order_puts_the_manifest_first():
    plan = plan_unpublish(
        collection=COLLECTION,
        subject=STOREY_SUBJECT,
        revision=STOREY_REVISION,
        collection_keys=_storey_keys(),
        manifest_bytes={},
    )
    assert not plan.refused
    assert plan.deleted[0] == _key(STOREY_SUBJECT, STOREY_REVISION, MANIFEST_FILENAME)
    assert set(plan.deleted[1:]) == {
        _key(STOREY_SUBJECT, STOREY_REVISION, "hierarchy.json"),
        _key(STOREY_SUBJECT, STOREY_REVISION, "source.ifc"),
    }


# --------------------------------------------------------------------------------------------
# An unreadable manifest is treated as holding everything it might have named -- the cautious
# direction, because a delete that should have been fine is recoverable and a dangling manifest
# is not.
# --------------------------------------------------------------------------------------------


def test_an_unreadable_manifest_refuses_cautiously():
    bad_key = _key("other-subject", "20260920T000000Z", MANIFEST_FILENAME)

    plan = plan_unpublish(
        collection=COLLECTION,
        subject=STOREY_SUBJECT,
        revision=STOREY_REVISION,
        collection_keys=_storey_keys(),
        manifest_bytes={bad_key: b"not a manifest, not even json"},
    )

    assert plan.refused
    assert plan.deleted == ()
    assert bad_key in plan.unreadable
    assert "could not be read" in plan.reason


# --------------------------------------------------------------------------------------------
# A subject-revision that was never published is reported as absent, not as an empty success --
# the caller must be able to tell "nothing to do" from "there was nothing here to begin with".
# --------------------------------------------------------------------------------------------


def test_a_subject_revision_that_does_not_exist_is_reported_as_absent_not_as_a_success():
    plan = plan_unpublish(
        collection=COLLECTION,
        subject="never-published",
        revision=STOREY_REVISION,
        collection_keys=_storey_keys(),  # some OTHER subject's keys are present in the collection
        manifest_bytes={},
    )
    assert plan.refused
    assert plan.reason is not None and "nothing published" in plan.reason
    assert plan.deleted == ()
    assert plan.kept == ()  # distinguishes "absent" from "held" -- both refuse, for different reasons
    assert plan.held_by == ()
