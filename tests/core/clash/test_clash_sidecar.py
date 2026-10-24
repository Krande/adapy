"""The convex-contact run sidecar (``ada.clash.sidecar``): what a clash run writes, and what a detail
job and a reloaded panel read back."""

from __future__ import annotations

import json

import pytest

from ada.clash.sidecar import (
    CLASH_SIDECAR_PREFIX,
    SidecarError,
    contact_id,
    group_geometry_sha256,
    index_key,
    index_with,
    parse_index,
    parse_run,
    run_from_glb_contacts,
    run_key,
    target_token,
)

TARGET = {"kind": "file", "source_key": "models/frame.ifc"}
GEOMETRY = {"key": "_derived/models/frame.ifc.glb", "sha256": "ab" * 32, "up_axis": "z"}
OPTIONS = {"security_margin": 0.01, "min_contact_area": 1e-6, "line_contact_thickness": 1e-3}


def _found():
    """``adacpp.clash.find_glb_contacts``'s shape: two members, one face contact (2 mm overlap)."""
    return {
        "members": [("4", "col"), ("7", "beam")],
        "pairs": [[0, 1]],
        "near1": [[0.2, 0.1, 2.6]],
        "near2": [[0.198, 0.1, 2.6]],
        "normal": [[1.0, 0.0, 0.0]],
        "penetration_depth": [-0.002],
        "contact_area": [0.02],
        "patches": [
            [
                {
                    "normal": (1.0, 0.0, 0.0),
                    "points1": [[0.2, 0.05, 2.5], [0.2, 0.15, 2.5], [0.2, 0.15, 2.7], [0.2, 0.05, 2.7]],
                    "points2": [[0.198, 0.05, 2.5], [0.198, 0.15, 2.5], [0.198, 0.15, 2.7], [0.198, 0.05, 2.7]],
                }
            ]
        ],
    }


def _run(**kw):
    return run_from_glb_contacts(
        _found(),
        run_id=kw.get("run_id", "r1"),
        created_at=kw.get("created_at", "2026-10-14T12:00:00Z"),
        engine="wasm",
        target=TARGET,
        geometry=GEOMETRY,
        options=OPTIONS,
        provenance={"adacpp": "0.34.0"},
    )


def test_a_run_round_trips_through_json():
    run = _run()
    back = parse_run(run.to_json())
    assert back == run
    (c,) = back.contacts
    assert c.id == contact_id(["4", "7"])
    assert back.names(c) == ("col", "beam")
    assert c.distance == -0.002 and c.penetration_depth == 0.002
    assert c.centre == pytest.approx((0.199, 0.1, 2.6))
    assert len(c.patches[0].points1) == 4


def test_the_clash_id_is_the_element_ids_not_the_names():
    # Two members called "beam" are still two members: the id hashes element ids, in any order.
    assert contact_id(["7", "4"]) == contact_id(["4", "7"])
    assert contact_id(["4", "7"]) != contact_id(["4", "8"])
    doc = _run().to_dict()
    doc["contacts"][0]["id"] = "000000000000"
    with pytest.raises(SidecarError, match="not the id of its members"):
        parse_run(doc)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.update(schema="ada.clash/result@2"), "not a clash sidecar"),
        (lambda d: d.pop("geometry"), "no 'geometry'"),
        (lambda d: d["geometry"].pop("sha256"), "sha256"),
        (lambda d: d["target"].pop("kind"), "kind"),
        (lambda d: d["contacts"][0].update(members=[0, 5]), "do not name two members"),
        (lambda d: d["contacts"][0].update(near_points=[[0, 0, 0]]), "two near points"),
        (lambda d: d["members"][0].pop("name"), "an id and a name"),
        (lambda d: d["contacts"][0].update(roles={"incoming": 0}), "incoming and landing"),
        (lambda d: d["contacts"][0].update(roles={"incoming": 0, "landing": 0}), "one each"),
    ],
)
def test_a_malformed_run_is_refused_with_a_reason(mutate, match):
    doc = json.loads(_run().to_json())
    mutate(doc)
    with pytest.raises(SidecarError, match=match):
        parse_run(doc)


def test_a_clashs_roles_round_trip_and_are_omitted_when_unresolved():
    doc = _run().to_dict()
    assert "roles" not in doc["contacts"][0]
    doc["contacts"][0]["roles"] = {"incoming": 1, "landing": 0}
    run = parse_run(doc)
    assert run.contacts[0].roles == {"incoming": 1, "landing": 0}
    assert parse_run(run.to_json()) == run
    assert run.to_dict()["contacts"][0]["roles"] == {"incoming": 1, "landing": 0}


def _box(lo, hi):
    """The eight corners of an axis-aligned box -- a member's mesh as the GLB gives its vertices."""
    import itertools

    import numpy as np

    return np.array(list(itertools.product(*zip(lo, hi))), dtype=float)


#: The fixture's two members as meshes: "col" (member 0) standing from z=0 to 3, "beam" (member 1)
#: running along +x from its end face at x=0.198 -- the end the contact (near1 = (0.2, 0.1, 2.6)) is at.
COL_MESH = _box((-0.1, -0.05, 0.0), (0.2, 0.25, 3.0))
BEAM_MESH = _box((0.198, -0.05, 2.45), (3.0, 0.25, 2.75))


def test_mesh_anchors_read_a_beam_by_its_ends_and_a_stubby_part_by_its_centroid():
    import numpy as np

    from ada.clash.contacts import mesh_anchors

    (p0, e0), (p1, e1) = mesh_anchors(BEAM_MESH)
    assert {e0, e1} == {"start", "end"}
    assert sorted([p0[0], p1[0]]) == pytest.approx([0.198, 3.0])
    (c0, n0), (c1, n1) = mesh_anchors(_box((0, 0, 0), (1.0, 0.8, 0.02)))  # a plate
    assert n0 is None and n1 is None and np.allclose(c0, (0.5, 0.4, 0.01)) and np.allclose(c0, c1)
    assert mesh_anchors([[0, 0, 0]]) is None


def test_roles_come_from_the_meshes_when_the_model_does_not_rebuild():
    from ada.clash.contacts import annotate_applicable

    asked = []

    def vertices():
        asked.append(1)
        return {0: COL_MESH, 1: BEAM_MESH}

    annotated, unbound = annotate_applicable(_run(), None, vertices=vertices)
    assert unbound == 1 and asked == [1]
    (c,) = annotated.contacts
    # The beam's end is at the contact: it comes in, the column takes it.
    assert c.roles == {"incoming": 1, "landing": 0}
    assert c.applicable == ()
    assert annotated.provenance["roles"] == {"members": 0, "mesh": 1, "unresolved": 0}
    assert parse_run(annotated.to_json()).contacts[0].roles == c.roles
    # Without meshes the roles stay unresolved -- and are not written.
    bare, _ = annotate_applicable(_run(), None, vertices={})
    assert bare.contacts[0].roles is None and "roles" not in bare.to_dict()["contacts"][0]
    assert bare.provenance["roles"] == {"members": 0, "mesh": 0, "unresolved": 1}


def test_member_vertices_reads_each_parts_glb_by_element_and_skips_a_changed_one():
    import hashlib

    from ada.clash.contacts import member_vertices

    glbs = {"_derived/models/a.ifc.glb": b"A", "_derived/assets/b.glb": b"B"}
    doc = _group_doc()
    for part in doc["geometry"]["parts"]:
        part["sha256"] = hashlib.sha256(glbs[part["key"]]).hexdigest()
    doc["geometry"]["sha256"] = group_geometry_sha256(doc["geometry"]["parts"])
    run = parse_run(doc)
    # Both GLBs carry an element "4" and a "7"; each member is taken from its own part only.
    by_glb = {
        b"A": [("4", "col", COL_MESH), ("7", "x", BEAM_MESH)],
        b"B": [("4", "y", COL_MESH), ("7", "beam", BEAM_MESH)],
    }
    seen_axes = []

    def read(glb, up_axis):
        seen_axes.append(up_axis)
        return by_glb[glb]

    got = member_vertices(run, glbs.__getitem__, read)
    assert set(got) == {0, 1} and got[0] is COL_MESH and got[1] is BEAM_MESH and seen_axes == ["z", "z"]
    glbs["_derived/assets/b.glb"] = b"changed"
    assert set(member_vertices(run, glbs.__getitem__, lambda g, a: by_glb.get(g, []))) == {0}

    # A one-GLB run's members are its element ids.
    single = _run()
    one = {GEOMETRY["key"]: b"G"}
    single = parse_run({**single.to_dict(), "geometry": {**GEOMETRY, "sha256": hashlib.sha256(b"G").hexdigest()}})
    got = member_vertices(single, one.__getitem__, lambda g, a: [("7", "beam", BEAM_MESH), ("4", "col", COL_MESH)])
    assert got[0] is COL_MESH and got[1] is BEAM_MESH


def test_keys_group_runs_by_target():
    assert run_key(TARGET, "r1").startswith(CLASH_SIDECAR_PREFIX + target_token(TARGET) + "/")
    assert index_key(TARGET) == f"{CLASH_SIDECAR_PREFIX}{target_token(TARGET)}/index.json"
    other = {"kind": "file", "source_key": "models/other.ifc"}
    assert target_token(other) != target_token(TARGET)
    # The descriptor's key order does not matter.
    assert target_token({"source_key": "models/frame.ifc", "kind": "file"}) == target_token(TARGET)


def test_the_index_lists_newest_first_and_replaces_a_rewritten_run():
    index = None
    for i, when in enumerate(["2026-10-14T10:00:00Z", "2026-10-14T12:00:00Z", "2026-10-14T11:00:00Z"]):
        run = _run(run_id=f"r{i}", created_at=when)
        index = index_with(index, run.summary(run_key(TARGET, run.run_id)))
    assert [r["run_id"] for r in parse_index(index)] == ["r1", "r2", "r0"]
    rewritten = _run(run_id="r0", created_at="2026-10-14T13:00:00Z")
    index = index_with(json.dumps(index), rewritten.summary(run_key(TARGET, "r0")))
    assert [r["run_id"] for r in parse_index(index)] == ["r0", "r1", "r2"]
    assert parse_index(index)[0]["contacts"] == 1
    assert parse_index(None) == []


GROUP_TARGET = {"kind": "group", "group_id": "g1"}
FILE_A = {"kind": "file", "source_key": "models/a.ifc"}
NODE_B = {"kind": "node", "provider": "prov", "collection": "plant", "subject": "S10", "revision": None, "node": None}


def _group_doc() -> dict:
    """A group run as the browser posts it: two GLBs, one member from each, one clash between them."""
    parts = [
        {"key": "_derived/models/a.ifc.glb", "sha256": "ab" * 32, "source": FILE_A},
        {"key": "_derived/assets/b.glb", "sha256": "cd" * 32, "source": NODE_B},
    ]
    doc = _run().to_dict()
    doc["target"] = dict(GROUP_TARGET)
    doc["geometry"] = {"key": "group:g1", "sha256": group_geometry_sha256(parts), "up_axis": "z", "parts": parts}
    doc["members"] = [
        {"id": "0:4", "name": "col", "element": "4", "part": 0},
        {"id": "1:7", "name": "beam", "element": "7", "part": 1},
    ]
    doc["contacts"][0]["id"] = contact_id(["0:4", "1:7"])
    doc["group"] = {
        "id": "g1",
        "name": "Deck 3",
        "members": [
            {"target": FILE_A, "element": "col", "path": ["col"]},
            {"target": NODE_B, "element": None, "path": []},
        ],
    }
    return doc


def test_a_group_run_keeps_its_parts_members_and_group():
    run = parse_run(_group_doc())
    assert [(m["part"], m["element"]) for m in run.members] == [(0, "4"), (1, "7")]
    assert [p["source"]["kind"] for p in run.parts] == ["file", "node"]
    assert run.group["name"] == "Deck 3" and len(run.group["members"]) == 2
    assert parse_run(run.to_json()) == run
    # The browser's value for the same parts (contactRuns.test.ts), order-free.
    assert (
        group_geometry_sha256(
            [{"key": "_derived/b.ifc.glb", "sha256": "cd" * 32}, {"key": "_derived/a.ifc.glb", "sha256": "ab" * 32}]
        )
        == "58d4618b8f0bb9e587240a47fb2f984f9be651248a313ea604c92603408b1937"
    )
    # Its runs list under the group id alone: a rename or a membership edit keeps them together.
    assert index_key(GROUP_TARGET) == index_key({"group_id": "g1", "kind": "group"})
    # A run over one GLB is untouched: no group, no parts.
    assert _run().group is None and _run().parts == () and "group" not in _run().to_dict()


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["target"].pop("group_id"), "group_id"),
        (lambda d: d.pop("group"), "carry the group"),
        (lambda d: d["group"].update(id="g2"), "carry the group"),
        (lambda d: d["geometry"].pop("parts"), "parts"),
        (lambda d: d["geometry"].update(sha256="ab" * 32), "hash of its parts"),
        (lambda d: d["members"][1].update(part=5), "part and element"),
        (lambda d: d["members"][1].update(id="7"), "'<part>:<element id>'"),
    ],
)
def test_a_malformed_group_run_is_refused(mutate, match):
    doc = _group_doc()
    mutate(doc)
    with pytest.raises(SidecarError, match=match):
        parse_run(doc)


def test_a_group_run_binds_each_member_in_its_own_parts_model():
    from ada.clash.contacts import bind_members

    class _Obj:
        def __init__(self, name):
            self.name = name

    class _Model:
        def __init__(self, *names):
            self.objects = [_Obj(n) for n in names]

        def get_all_physical_objects(self):
            return iter(self.objects)

    run = parse_run(_group_doc())
    a, b = _Model("col", "beam"), _Model("beam")
    binding = bind_members(run, [a, b])
    # "beam" is in both models; the member binds in its own part's model only.
    assert binding.member(0) is a.objects[0] and binding.member(1) is b.objects[0]
    missing = bind_members(run, [a, None])
    assert missing.member(1) is None and "part 1 did not rebuild" in missing.reason(1)


def test_a_run_from_a_real_viewer_glb(tmp_path):
    clash = pytest.importorskip("adacpp.clash")
    if not hasattr(clash, "find_glb_contacts"):
        pytest.skip("this adacpp has no find_glb_contacts")
    import ada

    model = ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("col", (0, 0, 0), (0, 0, 3), "BOX300x300x12x12"),
            ada.Beam("beam", (0.148, 0, 2.5), (3.0, 0, 2.5), "BOX300x300x12x12"),
        ]
    )
    glb = tmp_path / "frame.glb"
    model.to_gltf(glb)
    run = run_from_glb_contacts(
        clash.find_glb_contacts(glb),
        run_id="r1",
        created_at="2026-10-14T12:00:00Z",
        engine="worker",
        target=TARGET,
        geometry={"key": "x", "sha256": "ab" * 32, "up_axis": "z"},
        options=OPTIONS,
    )
    assert {m["name"] for m in run.members} >= {"col", "beam"}
    assert len(run.contacts) == 1
    assert parse_run(run.to_json()) == run
