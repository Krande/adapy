"""Phase 3b: loft-native GraphFace identity (``loft_face_id``).

A loft band cell's faces fall outside the axis-aligned ``GOLDEN_SIDE_ORDER`` that
names box faces, so they get their own stable id derived from the profile geometry:
``"{member}:bay{bay}:edge{k}"`` for the swept panel of profile edge ``k``, and
``"...:cap_lo"`` / ``"...:cap_hi"`` for the two end caps. These tests assert the id
scheme is deterministic, complete (side count == profile edge count + 2 caps), encodes
(member, bay, edge)/(cap), and that the plate build path (``loft_member_to_part``) names
plates by the same id and drops exactly the excluded faces. The box ``stable_face_id``
path must stay untouched (regression guard).
"""

from __future__ import annotations

import ada
from ada.geom.curves import PolyLoop
from ada.geom.points import Point
from ada.topology.io import LoftMember, from_section_loft, loft_member_to_part


def _rect(z: float, hw: float, hh: float) -> PolyLoop:
    return PolyLoop(polygon=[Point(-hw, -hh, z), Point(hw, -hh, z), Point(hw, hh, z), Point(-hw, hh, z)])


def _circle(z: float, r: float, n: int) -> PolyLoop:
    import math

    return PolyLoop(
        polygon=[Point(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
    )


def _ids(cell) -> list[str]:
    return sorted(f.loft_face_id for f in cell.faces if f.loft_face_id is not None)


# --- id completeness + shape ---------------------------------------------- #
def test_band_face_ids_side_count_plus_two_caps():
    # 3-station rectangle -> 2 bays; each band = 4 side panels (profile edges) + 2 caps.
    profiles = [_rect(0.0, 1.0, 1.0), _rect(2.0, 0.9, 0.9), _rect(5.0, 0.6, 0.6)]
    cg = from_section_loft([LoftMember("Taper", profiles)])
    assert len(cg.cells) == 2

    for cell in cg.cells:
        bay = cell.metadata.get("station_lo")
        ids = _ids(cell)
        # every face matched (no unmatched faces)
        assert all(f.loft_face_id is not None for f in cell.faces)
        # 4 profile edges + 2 caps
        sides = [i for i in ids if ":edge" in i]
        caps = [i for i in ids if ":cap_" in i]
        assert len(sides) == 4  # rectangle has 4 profile edges
        assert sorted(caps) == [f"Taper:bay{bay}:cap_hi", f"Taper:bay{bay}:cap_lo"]
        # edges numbered 0..3, encode member + bay
        assert sides == [f"Taper:bay{bay}:edge{k}" for k in range(4)]


def test_circle_member_side_count_matches_segments():
    # An 8-segment circle band has 8 profile-edge side panels + 2 caps.
    profiles = [_circle(0.0, 1.0, 8), _circle(4.0, 0.6, 8)]
    cg = from_section_loft([LoftMember("Pipe", profiles)])
    assert len(cg.cells) == 1
    cell = cg.cells[0]
    ids = _ids(cell)
    sides = [i for i in ids if ":edge" in i]
    caps = [i for i in ids if ":cap_" in i]
    assert len(sides) == 8
    assert len(caps) == 2
    assert all(f.loft_face_id is not None for f in cell.faces)


# --- determinism ----------------------------------------------------------- #
def test_face_ids_deterministic_across_two_builds():
    profiles = [_rect(0.0, 1.0, 0.8), _rect(3.0, 0.7, 0.5)]
    a = from_section_loft([LoftMember("M", profiles)])
    b = from_section_loft([LoftMember("M", profiles)])
    assert sorted(a.loft_face_map().keys()) == sorted(b.loft_face_map().keys())
    # and the SAME id maps to the SAME physical face (matched by centroid).
    amap, bmap = a.loft_face_map(), b.loft_face_map()
    for fid, fa in amap.items():
        ca, cb = fa.get_centroid(), bmap[fid].get_centroid()
        assert ca.is_equal(cb)


def test_loft_face_map_exposes_every_band_face():
    profiles = [_rect(0.0, 1.0, 1.0), _rect(2.0, 0.8, 0.8), _rect(4.0, 0.6, 0.6)]
    cg = from_section_loft([LoftMember("M", profiles)])
    fmap = cg.loft_face_map()
    # 2 bays x (4 edges + 2 caps) = 12 distinct ids.
    assert len(fmap) == 12
    for fid, face in fmap.items():
        assert face.loft_face_id == fid


def test_placement_member_faces_still_matched():
    import numpy as np

    place = np.eye(4)
    place[0, 3] = 10.0
    profiles = [_rect(0.0, 1.0, 1.0), _rect(3.0, 0.6, 0.6)]
    cg = from_section_loft([LoftMember("Shift", profiles, placement=place)])
    cell = cg.cells[0]
    assert all(f.loft_face_id is not None for f in cell.faces)
    # geometry actually placed at x ~= 10.
    assert abs(cell.get_centroid().x - 10.0) < 1e-6


# --- plate build path: naming + exclude ------------------------------------ #
def test_loft_member_to_part_names_plates_by_face_id():
    profiles = [_rect(0.0, 1.0, 1.0), _rect(2.0, 0.9, 0.9), _rect(5.0, 0.6, 0.6)]
    part = loft_member_to_part("Taper", profiles, thickness=0.02)
    names = sorted(p.name for p in part.get_all_physical_objects(by_type=ada.Plate))
    # 2 bays x 4 side panels + 2 external caps (interior divider is not plated) = 10.
    assert len(names) == 10
    assert "Taper:bay0:cap_lo" in names and "Taper:bay1:cap_hi" in names
    assert all(":bay" in n for n in names)


def test_exclude_drops_exactly_the_addressed_plate():
    profiles = [_rect(0.0, 1.0, 1.0), _rect(2.0, 0.9, 0.9), _rect(5.0, 0.6, 0.6)]
    full = loft_member_to_part("Taper", profiles, thickness=0.02)
    excl = loft_member_to_part("Taper", profiles, thickness=0.02, exclude_faces=["bay0:edge2"])

    full_names = {p.name for p in full.get_all_physical_objects(by_type=ada.Plate)}
    excl_names = {p.name for p in excl.get_all_physical_objects(by_type=ada.Plate)}
    assert full_names - excl_names == {"Taper:bay0:edge2"}
    assert len(excl_names) == len(full_names) - 1

    # geometry of the surviving plates is unchanged (same points per id).
    import numpy as np

    full_by = {p.name: p for p in full.get_all_physical_objects(by_type=ada.Plate)}
    for p in excl.get_all_physical_objects(by_type=ada.Plate):
        assert np.allclose(np.asarray(p.poly.points3d), np.asarray(full_by[p.name].poly.points3d))


def test_cap_exclude_drops_a_cap_plate():
    profiles = [_rect(0.0, 1.0, 1.0), _rect(3.0, 0.6, 0.6)]
    full = loft_member_to_part("C", profiles)
    excl = loft_member_to_part("C", profiles, exclude_faces=["bay0:cap_lo"])
    full_names = {p.name for p in full.get_all_physical_objects(by_type=ada.Plate)}
    excl_names = {p.name for p in excl.get_all_physical_objects(by_type=ada.Plate)}
    assert full_names - excl_names == {"C:bay0:cap_lo"}


# --- regression guard: box cells keep stable_face_id, no loft id ----------- #
def test_box_cells_untouched_by_loft_id_scheme():
    box = ada.PrimBox("b", (0, 0, 0), (2, 2, 2))
    from ada.topology.graph import CellGraph

    cg = CellGraph.from_prim_boxes([box])
    cell = cg.cells[0]
    # box faces get the golden stable_face_id and NO loft_face_id.
    assert all(f.loft_face_id is None for f in cell.faces)
    assert sorted(f.stable_face_id for f in cell.faces) == [0, 1, 2, 3, 4, 5]
    # loft_face_map is empty for a pure box graph.
    assert cg.loft_face_map() == {}
