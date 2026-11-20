"""A weld's metadata in a GLB's design extension says what the viewer's inspector shows for a picked
weld: its type, throat and members, how long it is and where it runs (``length``, ``at``), and -- in the
connection entries -- which joint it is a weld of, also when its builder keeps it in a plain sub-part of
the connection, and the roles a connection built at a model's joint recorded for its members."""

import ada
from ada import Beam, Part, Weld
from ada.api.connections import Connection
from ada.visit.scene_handling.scene_from_part import (
    _weld_metadata,
    build_design_data_extension,
)


def _fillet(name, p1, p2, members):
    return Weld(
        name,
        p1=p1,
        p2=p2,
        weld_type="FILLET",
        members=members,
        throat=0.005,
        xdir=(0, 1, 0),
    )


def _ext(part) -> dict:
    ext = build_design_data_extension(part, face_nodes=[])
    return ext.model_dump() if hasattr(ext, "model_dump") else ext


def test_a_welds_metadata_has_its_length_and_the_point_halfway_along_it():
    a = Beam("a", (0, 0, 0), (2, 0, 0), "IPE300")
    b = Beam("b", (2, 0, 0), (2, 0, 1), "IPE300")
    w = _fillet("w1", (2, 0, 0), (2, 0, 0.5), [a, b])
    meta = _weld_metadata(w)
    assert meta["type"] == "weld" and meta["weld_type"] == "FILLET" and meta["throat"] == 0.005
    assert meta["members"] == ["a", "b"]
    assert abs(meta["length"] - 0.5) < 1e-9
    assert meta["at"] == [2.0, 0.0, 0.25]


def test_a_weld_kept_in_a_plain_sub_part_of_a_connection_belongs_to_that_connection():
    a = Beam("a", (0, 0, 0), (2, 0, 0), "IPE300")
    b = Beam("b", (2, 0, 0), (2, 0, 1), "IPE300")
    conn = Connection("spec.x_j1")
    welds = Part("welds")
    conn.add_part(welds)
    welds.add_weld(_fillet("weld2681", (2, 0, 0), (2, 0, 0.5), [a, b]))
    conn.metadata["connection_info"] = {
        "name": conn.name,
        "spec_name": "spec.x",
        "member_roles": {"landing": ["a"], "incoming": ["b"]},
    }
    joints = Part("Joints")
    joints.add_part(conn)
    asm = ada.Assembly("m") / [Part("members") / [a, b], joints]

    ext = _ext(asm)
    design = ext["design_objects"][0] if "design_objects" in ext else ext
    assert design["object_metadata"]["weld2681"]["members"] == ["a", "b"]
    (entry,) = [c for c in design["connections"] if c["name"] == "spec.x_j1"]
    assert entry["weld_names"] == ["weld2681"]
    assert entry["spec_name"] == "spec.x"
    assert entry["member_roles"] == {"landing": ["a"], "incoming": ["b"]}


def test_a_nested_connection_keeps_its_own_welds():
    a = Beam("sample_incoming", (0, 0, 0), (2, 0, 0), "IPE300")
    b = Beam("sample_landing", (2, 0, 0), (2, 0, 1), "IPE300")
    outer = Connection("outer")
    outer.add_beam(a)
    outer.add_beam(b)
    inner = Connection("inner")
    outer.add_part(inner)
    inner.add_weld(_fillet("w1", (2, 0, 0), (2, 0, 0.5), [a, b]))

    ext = _ext(ada.Assembly("m") / outer)
    design = ext["design_objects"][0] if "design_objects" in ext else ext
    by_name = {c["name"]: c for c in design["connections"]}
    assert by_name["outer"]["weld_names"] is None
    assert by_name["outer"]["member_roles"] == {"incoming": ["sample_incoming"], "landing": ["sample_landing"]}
    assert by_name["inner"]["weld_names"] == ["w1"]
