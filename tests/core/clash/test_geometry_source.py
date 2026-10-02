"""Checking members with ANOTHER provider's geometry of them (``ClashOptions.geometry_provider`` /
``ada.clash.geometry_source``).

The fixture is one collection two providers published into, each with its own node ids for the
same elements: a MESH-ONLY provider (opaque ids derived from the names, no reader anywhere) and a
MEMBER-READER (its own refs, and a concepts reader that yields beams). A group picked in the
mesh-only tree cannot be read as it stands; with the member-reader as geometry provider each member
is matched to the member-reader's node by NAME and read from there.

What is pinned: the option's wire form, the match rules (leading ``/`` optional, case-sensitive
before case-insensitive, ambiguity refused, a mixed index's other-provider rows ignored), that an
unmatched member is a warning and not the check's failure, that the result records where the
geometry came from, and that a detail rebuild from the recorded members reproduces the joint ids.
"""

from __future__ import annotations

import contextlib
import json
import pathlib

import pytest

with contextlib.suppress(ImportError):
    import adacpp  # noqa: F401  (see test_group_check.py: numpy's BLAS on an un-activated Windows env)

import ada
from ada.assets.concepts import clear_asset_concepts, register_asset_concepts
from ada.assets.keys import asset_key
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy
from ada.clash import ClashOptions
from ada.clash.detail import joints_by_id
from ada.clash.from_asset import clash_check_from_asset_node
from ada.clash.geometry_source import (
    GeometrySourceIndex,
    normalise_label,
    remap_group,
    remap_to_provider,
)
from ada.clash.group import normalise_group
from ada.clash.group_model import GroupModelError, build_group_model, clash_check_group

COLLECTION = "plant"
MESH = "mesh-only"
READER = "member-reader"
R1 = "20260901T000000Z"
R2 = "20260902T000000Z"
OPTIONS = ClashOptions(include_plate_joints=False, geometry_provider=READER)


class _Storage:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.reads: list[str] = []

    def get_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        return self.blobs[key]

    def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self.blobs if k.startswith(prefix)]

    def fetch_to_path(self, key: str, dest):
        pathlib.Path(dest).write_bytes(self.blobs[key])
        return dest


def _publish(s: _Storage, *, provider, subject, revision, nodes, build_source=None, node=None) -> None:
    spine = build_hierarchy(provider=provider, collection=COLLECTION, produced_at=revision, nodes=nodes)
    s.blobs[asset_key(COLLECTION, subject, revision, HIERARCHY_FILENAME)] = spine.to_json()
    artefacts = [ArtefactEntry(role="hierarchy", file=HIERARCHY_FILENAME, sha256="", size=1)]
    build = None
    if build_source is not None:
        key = asset_key(COLLECTION, subject, revision, "lines.txt")
        s.blobs[key] = build_source
        artefacts.append(ArtefactEntry(role="source", key=key, sha256="", size=len(build_source)))
        build = BuildSpec(capability="build-lines", options={"source_key": key})
    manifest = AssetManifest(
        provider=provider,
        collection=COLLECTION,
        subject=subject,
        revision=revision,
        node=node if node is not None else (None if subject == COLLECTION else subject),
        produced_at=revision,
        published_at=revision,
        delivery="build" if build else "mesh",
        build=build,
        artefacts=tuple(artefacts),
    )
    s.blobs[asset_key(COLLECTION, subject, revision, MANIFEST_FILENAME)] = manifest.to_json()


def _row(node_id, parent, label, provider=None, leaf=True) -> dict:
    row = {"id": node_id, "parent": parent, "label": label, "kind": "item", "leaf": leaf}
    if provider:
        row["provider"] = provider
    return row


LINES = b"\n".join(
    [
        b"Z100-BEAMS2 0 0 0 2 0 0",
        b"Z100-BEAMS3 1 0 0 1 2 0",
        b"Z100-FAR 50 50 0 52 50 0",
    ]
)


@pytest.fixture
def storage() -> _Storage:
    s = _Storage()
    # The collection index: the member-reader's OLDER publish is a mixed spine naming the mesh-only
    # zone under the same label -- which must not make the match ambiguous -- and the mesh-only
    # provider's newer one shadows it as "latest".
    _publish(
        s,
        provider=READER,
        subject=COLLECTION,
        revision=R1,
        nodes=[_row("ref-100", None, "/Z100-ZONE", READER, False), _row("fnzone", None, "/Z100-ZONE", MESH, False)],
    )
    _publish(s, provider=MESH, subject=COLLECTION, revision=R2, nodes=[_row("fnzone", None, "/Z100-ZONE", leaf=False)])
    # The mesh-only zone: names as the viewer shows them, opaque ids, nothing to read.
    _publish(
        s,
        provider=MESH,
        subject="fnzone",
        revision=R2,
        nodes=[
            _row("fnzone", None, "/Z100-ZONE", leaf=False),
            _row("fn01", "fnzone", "/Z100-BEAMS2"),
            _row("fn02", "fnzone", "/z100-beams3"),
            _row("fn03", "fnzone", "/DUP"),
            _row("fn04", "fnzone", "/LOST"),
        ],
    )
    # The member-reader's zone: its own refs, the same names (one without the slash, one in another
    # case), a name published twice, and a build that a reader can turn into beams.
    _publish(
        s,
        provider=READER,
        subject="ref-100",
        revision=R1,
        build_source=LINES,
        nodes=[
            _row("ref-100", None, "/Z100-ZONE", leaf=False),
            _row("ref-101", "ref-100", "Z100-BEAMS2"),
            _row("ref-102", "ref-100", "Z100-BEAMS3"),
            _row("ref-103", "ref-100", "/DUP"),
            _row("ref-104", "ref-100", "/DUP"),
            _row("ref-105", "ref-100", "Z100-FAR"),
        ],
    )
    return s


class _LinesConcepts:
    """The member-reader's reader: one beam per line, named as its spine labels them."""

    def concepts(self, options, *, storage, scope=None, node=None):
        part = ada.Part(node or "lines")
        for line in storage.get_bytes(options["source_key"]).decode().splitlines():
            name, *xyz = line.split()
            x0, y0, z0, x1, y1, z1 = (float(v) for v in xyz)
            part.add_beam(ada.Beam(name, (x0, y0, z0), (x1, y1, z1), "IPE200"))
        return part


@pytest.fixture
def reader():
    register_asset_concepts(READER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def _mesh_member(element: str | None) -> dict:
    target = {"kind": "node", "provider": MESH, "collection": COLLECTION, "subject": "fnzone", "revision": R2}
    return {"target": target, "element": element, "path": [element] if element else []}


def _group(*members) -> dict:
    return normalise_group({"name": "Deck 3", "members": list(members)})


# ── the option ────────────────────────────────────────────────────────


def test_the_option_round_trips_and_is_absent_by_default():
    assert "geometry_provider" not in ClashOptions().to_dict()
    raw = ClashOptions(geometry_provider=READER).to_dict()
    assert raw["geometry_provider"] == READER
    assert ClashOptions.from_dict(raw) == ClashOptions(geometry_provider=READER)
    # Blank is "each member's own", the same as absent, so it cannot split a cache entry.
    assert ClashOptions.from_dict({"geometry_provider": "  "}).geometry_provider is None
    with pytest.raises(TypeError, match="geometry_provider"):
        ClashOptions.from_dict({"geometry_provider": 3})


def test_label_normalisation():
    assert normalise_label(" /Z100-ZONE ") == "Z100-ZONE"
    assert normalise_label("Z100-ZONE") == "Z100-ZONE"


# ── the match ─────────────────────────────────────────────────────────


def test_an_element_is_matched_by_name_with_the_leading_slash_optional(storage):
    new, warning = remap_to_provider(_mesh_member("/Z100-BEAMS2"), READER, index=GeometrySourceIndex(storage))
    assert warning is None
    assert new == {
        "target": {
            "kind": "node",
            "provider": READER,
            "collection": COLLECTION,
            "subject": "ref-100",
            "revision": R1,
            "node": "ref-100",
        },
        # The reader's own spelling, and no path: the element is found in the read by name alone.
        "element": "Z100-BEAMS2",
        "path": [],
    }


def test_case_is_only_ignored_when_the_exact_name_finds_nothing(storage):
    new, warning = remap_to_provider(_mesh_member("/z100-beams3"), READER, index=GeometrySourceIndex(storage))
    assert warning is None
    assert new["element"] == "Z100-BEAMS3"


def test_a_whole_node_is_matched_by_its_published_label_and_read_whole(storage):
    # The mixed collection index also lists the mesh-only zone as "/Z100-ZONE"; only the reader's
    # own rows count, so this is one match and not an ambiguity.
    new, warning = remap_to_provider(_mesh_member(None), READER, index=GeometrySourceIndex(storage))
    assert warning is None
    assert new["target"]["subject"] == "ref-100" and new["target"]["node"] == "ref-100"
    assert new["element"] is None


def test_an_ambiguous_name_is_refused_with_both_candidates(storage):
    new, warning = remap_to_provider(_mesh_member("/DUP"), READER, index=GeometrySourceIndex(storage))
    assert new is None
    assert "2 nodes" in warning and "ref-103" in warning and "ref-104" in warning


def test_an_unknown_name_is_left_out_with_a_reason(storage):
    new, warning = remap_to_provider(_mesh_member("/LOST"), READER, index=GeometrySourceIndex(storage))
    assert new is None
    assert "'/LOST'" in warning and READER in warning


def test_a_provider_that_published_nothing_here_is_named(storage):
    new, warning = remap_to_provider(_mesh_member("/Z100-BEAMS2"), "nobody", index=GeometrySourceIndex(storage))
    assert new is None and "published nothing" in warning


def test_files_and_the_providers_own_nodes_are_left_alone(storage):
    index = GeometrySourceIndex(storage)
    file_member = {"target": {"kind": "file", "source_key": "a.ifc"}, "element": None, "path": []}
    assert remap_to_provider(file_member, READER, index=index) == (file_member, None)
    own = {
        "target": {
            "kind": "node",
            "provider": READER,
            "collection": COLLECTION,
            "subject": "ref-100",
            "node": "ref-100",
        },
        "element": "Z100-FAR",
        "path": [],
    }
    assert remap_to_provider(own, READER, index=index) == (own, None)


def test_a_group_is_remapped_with_every_read_cached(storage):
    remap = remap_group(
        _group(*(_mesh_member(e) for e in ("/Z100-BEAMS2", "/z100-beams3", "/DUP", "/LOST"))),
        READER,
        storage=storage,
    )
    assert len(remap.remapped) == 2
    assert len(remap.unmatched) == 2
    assert {m["element"] for m in remap.group["members"]} == {"Z100-BEAMS2", "Z100-BEAMS3"}
    assert len(remap.warnings) == 2
    # One read per blob, however many members asked.
    assert len(storage.reads) == len(set(storage.reads))


# ── the check ─────────────────────────────────────────────────────────


def test_without_a_geometry_provider_the_mesh_only_group_cannot_be_read(storage, reader):
    with pytest.raises(GroupModelError, match="could not be read"):
        clash_check_group(
            _group(_mesh_member("/Z100-BEAMS2"), _mesh_member("/z100-beams3")),
            token="t",
            storage=storage,
            options=ClashOptions(include_plate_joints=False),
        )


def test_the_group_is_checked_with_the_readers_geometry_and_says_so(storage, reader):
    asked = _group(*(_mesh_member(e) for e in ("/Z100-BEAMS2", "/z100-beams3", "/DUP", "/LOST")))
    doc = clash_check_group(asked, token="t", storage=storage, options=OPTIONS)

    assert doc["counts"]["beams"] == 2  # the far beam was not a member
    assert doc["counts"]["joints"] == 1
    assert {m["name"] for m in doc["joints"][0]["members"]} == {"Z100-BEAMS2", "Z100-BEAMS3"}
    prov = doc["provenance"]
    assert prov["geometry_provider"] == READER
    assert prov["requested_group"] == asked
    assert len(prov["geometry_remap"]) == 2 and len(prov["geometry_unmatched"]) == 2
    assert {m["target"]["provider"] for m in prov["group"]["members"]} == {READER}
    assert doc["options"]["geometry_provider"] == READER
    text = " | ".join(doc["warnings"])
    assert "'/DUP'" in text and "'/LOST'" in text
    json.dumps(doc)  # the document crosses a job boundary


def test_a_group_nothing_of_which_matches_fails_by_name(storage, reader):
    with pytest.raises(GroupModelError, match="could be matched to provider"):
        build_group_model(
            _group(_mesh_member("/LOST"), _mesh_member("/DUP")), storage=storage, geometry_provider=READER
        )


def test_a_detail_rebuild_from_the_recorded_members_reproduces_the_ids(storage, reader):
    doc = clash_check_group(
        _group(_mesh_member("/Z100-BEAMS2"), _mesh_member("/z100-beams3")),
        token="t",
        storage=storage,
        options=OPTIONS,
    )
    ids = {j["id"] for j in doc["joints"]}
    assert ids
    storage.reads.clear()
    # What the detail job does: rebuild from provenance.group, with no search.
    rebuilt = build_group_model(doc["provenance"]["group"], storage=storage).model
    assert not any(k.endswith(HIERARCHY_FILENAME) for k in storage.reads)
    assert ids <= set(joints_by_id(rebuilt, ClashOptions.from_dict(doc["options"])))


def test_a_single_node_is_checked_through_the_geometry_provider(storage, reader):
    doc = clash_check_from_asset_node(
        collection=COLLECTION, subject="fnzone", node="fnzone", storage=storage, options=OPTIONS
    )
    assert doc["counts"]["joints"] == 1
    prov = doc["provenance"]
    assert prov["provider"] == MESH and prov["geometry_provider"] == READER
    # Recorded as a one-member group, so its detail rebuilds the re-addressed node.
    (member,) = prov["group"]["members"]
    assert member["target"]["subject"] == "ref-100" and member["element"] is None
    rebuilt = build_group_model(prov["group"], storage=storage).model
    assert {j["id"] for j in doc["joints"]} <= set(joints_by_id(rebuilt, ClashOptions.from_dict(doc["options"])))
