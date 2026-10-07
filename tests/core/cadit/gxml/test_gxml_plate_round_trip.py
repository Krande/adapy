"""A GeniE plate is one adapy plate, whatever its ACIS body looks like.

A GeniE ``<flat_plate>``/``<curved_shell>`` names the ACIS faces it occupies, and a plate
that stiffeners or its neighbours imprinted names several. adapy reads such a plate as one
:class:`~ada.Plate` by merging the faces' outlines, and the SAT writer splits it again on
the way out, so the plate goes back to GeniE as one element over the same faces.

The fixtures are GeniE V9.3-00 workspaces saved twice, ACIS body text and binary (see
``test_from_gnx_binary_acis``); every case runs on both.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import ada
from ada.core.vector_utils import merge_coplanar_loops_by_edge_cancellation

BODIES = ("text", "binary")


@pytest.fixture
def genie93(fem_files) -> pathlib.Path:
    return fem_files / "sesam" / "genie93_acis"


def _plates(a: ada.Assembly) -> list:
    return sorted(a.get_all_physical_objects(by_type=(ada.Plate, ada.PlateCurved)), key=lambda p: p.name)


def _outline_area(pl: ada.Plate) -> float:
    pts = np.asarray(pl.poly.points2d, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# --- a plate split by a stiffener that ends on another (a T-junction) ----------------------
#
# GeniE: Plate(0,0 .. 4,3), Bm1 along x=2 over the full height, Bm2 along y=1.5 from x=2 to 4.
# The body holds 3 faces: the left half, whose edge along x=2 carries a vertex at (2, 1.5)
# where Bm2 meets Bm1, and the two right quarters. The left face's loop lists (2, 1.5) as a
# vertex on a straight line, the face reader drops it as collinear, and the right quarters'
# edges along x=2 then have nothing to cancel against. Measured before the fix: 3 plates
# (Pl1, Pl1_02, Pl1_03); GeniE holds 1.


def test_t_junction_loops_merge_only_when_split():
    left = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0)]
    lower_right = [(2, 0, 0), (4, 0, 0), (4, 1.5, 0), (2, 1.5, 0)]
    upper_right = [(2, 1.5, 0), (4, 1.5, 0), (4, 3, 0), (2, 3, 0)]
    loops = [left, lower_right, upper_right]

    assert merge_coplanar_loops_by_edge_cancellation(loops) is None
    merged = merge_coplanar_loops_by_edge_cancellation(loops, split_t_junctions=True)
    assert merged is not None
    assert sorted(tuple(round(c, 12) for c in p) for p in merged) == [(0, 0, 0), (0, 3, 0), (4, 0, 0), (4, 3, 0)]


def test_t_junction_split_leaves_a_vertex_off_the_edge_alone():
    # (2, 1.5 + 1e-3) is a millimetre off the left face's edge: not on it, so nothing to split and
    # the three loops do not form one outline.
    left = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0)]
    lower_right = [(2, 0, 0), (4, 0, 0), (4, 1.5, 0), (2.001, 1.5, 0)]
    upper_right = [(2.001, 1.5, 0), (4, 1.5, 0), (4, 3, 0), (2, 3, 0)]
    assert merge_coplanar_loops_by_edge_cancellation([left, lower_right, upper_right], split_t_junctions=True) is None


@pytest.mark.parametrize("body", BODIES)
def test_t_junction_plate_reads_as_one_plate(genie93, body):
    a = ada.from_gnx(genie93 / f"plate_t_junction_{body}.gnx")
    (pl,) = _plates(a)
    assert type(pl) is ada.Plate and pl.name == "Pl1"
    assert pl.metadata["props"]["gxml_face_refs"] == ["FACE00000001", "FACE00000002", "FACE00000003"]
    assert _outline_area(pl) == pytest.approx(12.0, rel=1e-12)
    assert len(list(a.get_all_physical_objects(by_type=ada.Beam))) == 2


@pytest.mark.parametrize("body", BODIES)
def test_t_junction_plate_writes_back_as_one_plate_over_three_faces(genie93, body, tmp_path):
    """The stiffeners split the merged plate again on the way out: one element, three faces."""
    from ada.cadit.sat.write.writer import part_to_sat_writer

    a = ada.from_gnx(genie93 / f"plate_t_junction_{body}.gnx")
    (pl,) = _plates(a)
    sw = part_to_sat_writer(a, imprint=True)
    assert len(sw.face_map[pl.guid]) == 3

    back = ada.from_gnx(a.to_gnx(tmp_path / "rt.gnx", binary_acis=body == "binary"))
    (rpl,) = _plates(back)
    assert rpl.name == "Pl1" and len(rpl.metadata["props"]["gxml_face_refs"]) == 3
    assert _outline_area(rpl) == pytest.approx(12.0, rel=1e-12)
