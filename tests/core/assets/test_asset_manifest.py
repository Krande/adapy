"""``asset.json`` -- the envelope core owns.

The load-bearing test here is the LAST one: a manifest with every optional carrier absent must
validate. That is what keeps the IFC borrowing honest -- core adopted IfcOwnerHistory's *shape*
for authorship, not a requirement that anyone supply it.
"""

import json

import pytest

from ada.assets.manifest import (
    MANIFEST_SCHEMA,
    Actor,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
    ChangeRecord,
    ManifestError,
    parse_manifest,
)

REV = "20260921T143001Z"


def _minimal(**over) -> AssetManifest:
    base = dict(
        provider="fixture-lines",
        collection="plant-a",
        subject="n1",
        revision=REV,
        produced_at="2026-09-21T14:29:00Z",
        published_at="2026-09-21T14:30:01Z",
    )
    base.update(over)
    return AssetManifest(**base)


def test_roundtrip_through_json():
    m = _minimal(
        node="n1",
        delivery="build",
        build=BuildSpec(capability="asset-build-csg", options={"lod": 2}, fingerprint_inputs=("source",)),
        artefacts=(ArtefactEntry(role="source", file="source.jsonl", sha256="ab" * 32, size=17),),
        counts={"objects": 3},
    )
    back = parse_manifest(m.to_json())
    assert back == m


def test_unknown_schema_is_refused_not_partially_read():
    doc = json.loads(_minimal().to_json())
    doc["schema"] = "ada.assets/manifest@2"
    with pytest.raises(ManifestError, match="unknown manifest schema"):
        parse_manifest(json.dumps(doc))


def test_missing_required_fields_are_named():
    doc = json.loads(_minimal().to_json())
    del doc["provider"], doc["produced_at"]
    with pytest.raises(ManifestError, match="provider.*produced_at|produced_at.*provider"):
        parse_manifest(json.dumps(doc))


def test_build_delivery_requires_a_build_spec():
    with pytest.raises(ManifestError, match="requires a build spec"):
        _minimal(delivery="build")
    with pytest.raises(ManifestError, match="not 'build'"):
        _minimal(delivery="mesh", build=BuildSpec(capability="x"))


def test_artefact_sets_exactly_one_of_file_or_key():
    """'key' is how N leaf manifests share one uploaded source blob without copying it N times."""
    with pytest.raises(ManifestError, match="exactly one of"):
        ArtefactEntry(role="source", sha256="a" * 64, size=1)
    with pytest.raises(ManifestError, match="exactly one of"):
        ArtefactEntry(role="source", sha256="a" * 64, size=1, file="s.jsonl", key="assets/c/s/r/s.jsonl")
    shared = ArtefactEntry(role="source", sha256="a" * 64, size=1, key=f"assets/plant-a/plant-a/{REV}/source.jsonl")
    assert shared.file is None


def test_opaque_roles_survive_a_roundtrip_uninterpreted():
    """Core carries a provider's roles and filenames without knowing what they mean."""
    m = _minimal(
        artefacts=(
            ArtefactEntry(role="vendor-private", file="whatever.bin", sha256="c" * 64, size=9, schema_version=7),
        )
    )
    entry = parse_manifest(m.to_json()).artefacts[0]
    assert (entry.role, entry.file, entry.schema_version) == ("vendor-private", "whatever.bin", 7)


def test_stored_location_and_recorded_identity_must_agree():
    from ada.assets.keys import parse_asset_key

    key = parse_asset_key(f"assets/plant-a/n1/{REV}/asset.json")
    assert _minimal().matches_key(key)
    assert not _minimal(subject="other").matches_key(key)


# --- authorship: shape borrowed, requirement not ------------------------------------------------


def test_core_stamped_and_provider_relayed_authorship_never_merge():
    m = _minimal(
        change=ChangeRecord(
            published_by=Actor(id="u-1", display="A User"),
            published_via="user",
            source_actor=Actor(id="vendor-7", application="TheirTool 2.1"),
            action="modified",
            source_instant="2026-09-20T09:00:00Z",
        )
    )
    c = parse_manifest(m.to_json()).change
    assert c.published_by.id == "u-1"  # verified: core stamped it
    assert c.source_actor.id == "vendor-7"  # unverifiable: the provider relayed it
    assert c.action == "modified"


@pytest.mark.parametrize("bad", [{"published_via": "robot"}, {"action": "renamed"}])
def test_change_vocabularies_are_closed(bad):
    with pytest.raises(ManifestError):
        ChangeRecord(**bad)


def test_manifest_with_every_optional_carrier_absent_validates():
    """The gate that keeps the IFC borrowing honest.

    No change record, no authorship, no action, no attributes artefact, no classification, no
    hierarchy_revision, no build, no counts -- a provider whose source carries none of it is
    first class, and this manifest is complete rather than degraded.
    """
    m = _minimal()
    assert m.change is None and m.build is None and m.node is None
    assert m.artefacts == () and dict(m.counts) == {}
    back = parse_manifest(m.to_json())
    assert back == m
    assert back.delivery == "none"
    assert json.loads(m.to_json())["schema"] == MANIFEST_SCHEMA
