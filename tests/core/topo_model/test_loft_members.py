"""Phase 2a: authored loft members in a procedural model.

``TopoLoftMember`` is the swept sibling of ``TopoSpace`` — an ordered stack of
``LoftStation`` section profiles. These tests cover the backend half of the
minimal read-only loft viewer:

  (a) doc round-trip — a ProceduralBuilder carrying loft_members survives
      to_doc -> from_dict with its station params + placement intact;
  (b) build — a loft-only doc builds to Sum(stations-1) band cells + non-empty
      plate geometry, and to_glb emits a valid GLB;
  (c) mixed — spaces + loft_members build together without error;
  (d) validate_doc (rest) accepts + preserves loft_members.
"""

from __future__ import annotations

import numpy as np

import ada
from ada.comms.rest.procedural import validate_doc
from ada.topo_model import ProceduralBuilder
from ada.topology.entities import LoftStation, TopoLoftMember, TopoSpace


def _column_member() -> TopoLoftMember:
    # 2-station equal-section column -> 1 band cell.
    return TopoLoftMember(
        NAME="Column",
        STATIONS=[
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=0.0, WIDTH=2.0, HEIGHT=2.0),
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=3.0, WIDTH=2.0, HEIGHT=2.0),
        ],
    )


def _tapered_member(placement: list[list[float]] | None = None) -> TopoLoftMember:
    # 3-station tapered rectangle -> 2 band cells.
    return TopoLoftMember(
        NAME="Taper",
        STATIONS=[
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=0.0, WIDTH=2.0, HEIGHT=2.0),
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=2.0, WIDTH=1.6, HEIGHT=1.6),
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=5.0, WIDTH=1.0, HEIGHT=1.0),
        ],
        PLACEMENT=placement,
        THICKNESS=0.02,
    )


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


# --- (a) doc round-trip ---------------------------------------------------- #
def test_loft_members_doc_round_trip():
    place = np.eye(4)
    place[0, 3] = 10.0  # translate the tapered member +10 in x
    pb = ProceduralBuilder(
        spaces=[],
        loft_members=[_column_member(), _tapered_member(place.tolist())],
    )
    doc = pb.to_doc()
    assert "loft_members" in doc and len(doc["loft_members"]) == 2

    pb2 = ProceduralBuilder.from_dict(doc)
    assert [m.NAME for m in pb2.loft_members] == ["Column", "Taper"]

    col, taper = pb2.loft_members
    assert len(col.STATIONS) == 2
    assert col.STATIONS[0].WIDTH == 2.0 and col.STATIONS[0].HEIGHT == 2.0
    assert col.PLACEMENT is None  # identity dropped by exclude_none

    assert len(taper.STATIONS) == 3
    assert [s.WIDTH for s in taper.STATIONS] == [2.0, 1.6, 1.0]
    assert taper.STATIONS[-1].Z == 5.0
    assert taper.THICKNESS == 0.02
    # placement preserved (the +10 x translation).
    assert taper.PLACEMENT is not None
    assert taper.PLACEMENT[0][3] == 10.0


def test_box_only_doc_has_no_loft_members_key():
    # Additive: a box-only model's doc is unchanged (no loft_members key).
    pb = ProceduralBuilder(spaces=[TopoSpace(NAME="C", X=0, Y=0, Z=0, DX=4, DY=4, DZ=3)])
    assert "loft_members" not in pb.to_doc()


# --- (b) loft-only build --------------------------------------------------- #
def test_loft_only_build_cells_plates_and_glb():
    pb = ProceduralBuilder(spaces=[], loft_members=[_column_member(), _tapered_member()])
    glb = pb.compile()

    # cell count == Sum(stations - 1) = (2-1) + (3-1) = 3
    assert pb.loft_cell_graph is not None
    assert len(pb.loft_cell_graph.cells) == 3
    members = {c.metadata.get("member") for c in pb.loft_cell_graph.cells}
    assert members == {"Column", "Taper"}

    # plate geometry is non-empty: a Lofts part with a sub-part of plates per member.
    lofts = pb.assembly.parts["Lofts"]
    plates = list(lofts.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) > 0

    # valid GLB.
    assert _is_glb(glb) and len(glb) > 500


def test_circle_station_member_builds():
    # A circle-sectioned member samples RADIUS into a polygon; still lofts.
    member = TopoLoftMember(
        NAME="Pipe",
        STATIONS=[
            LoftStation(TYPE="circle", X=0.0, Y=0.0, Z=0.0, RADIUS=1.0, SEGMENTS=12),
            LoftStation(TYPE="circle", X=0.0, Y=0.0, Z=4.0, RADIUS=0.6, SEGMENTS=12),
        ],
    )
    pb = ProceduralBuilder(spaces=[], loft_members=[member])
    glb = pb.compile()
    assert len(pb.loft_cell_graph.cells) == 1
    assert _is_glb(glb)


# --- (c) mixed spaces + loft_members --------------------------------------- #
def test_mixed_spaces_and_loft_members_build():
    pb = ProceduralBuilder(
        spaces=[TopoSpace(NAME="Cell1", X=0, Y=0, Z=0, DX=5, DY=5, DZ=3)],
        loft_members=[_tapered_member()],
    )
    glb = pb.compile()
    # the box structure built AND the loft plates present.
    assert pb.assembly is not None
    assert "Lofts" in pb.assembly.parts
    assert pb.loft_cell_graph is not None and len(pb.loft_cell_graph.cells) == 2
    assert _is_glb(glb)


# --- (d) rest validate_doc ------------------------------------------------- #
def test_validate_doc_accepts_and_preserves_loft_members():
    doc = {
        "loft_members": [
            _column_member().model_dump(mode="json", exclude_none=True),
            _tapered_member().model_dump(mode="json", exclude_none=True),
        ],
    }
    out = validate_doc(doc)
    assert len(out["loft_members"]) == 2
    assert out["loft_members"][0]["NAME"] == "Column"
    assert [s["WIDTH"] for s in out["loft_members"][1]["STATIONS"]] == [2.0, 1.6, 1.0]
