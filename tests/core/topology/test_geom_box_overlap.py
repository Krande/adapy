"""Overlap semantics of is_face_inside_box: touching is not overlapping.

A face that merely touches the query box along an edge or point (zero-area
contact) must not count as inside. The OCC kernel emits float noise on face
coordinates (e.g. 7.1500000000000012 where the box edge sits at 7.15), so the
overlap checks need a tolerance margin — strict comparisons would let that
noise flip grazing faces to "inside" and bind e.g. side-of-space queries to
faces of the neighbouring cell below.
"""

from __future__ import annotations

import numpy as np

import ada
from ada.topology import CellGraph
from ada.topology._geom import is_face_inside_box
from ada.topology.entities import TopoSpace


def _quad_x(x, y0, y1, z0, z1):
    """A planar quad at constant x."""
    return np.array([[x, y0, z0], [x, y1, z0], [x, y1, z1], [x, y0, z1]])


def test_face_inside_planar_box():
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert is_face_inside_box(_quad_x(2.0, 1.0, 9.0, 6.0, 11.0), box_p1, box_p2)


def test_face_coinciding_with_planar_box():
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert is_face_inside_box(_quad_x(2.0, 0.0, 10.0, 5.0, 12.0), box_p1, box_p2)


def test_grazing_face_below_planar_box_is_outside():
    # Face spans z in [0, 5]: it touches the box (z in [5, 12]) only along
    # the z=5 line — zero-area contact, must be outside.
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert not is_face_inside_box(_quad_x(2.0, 0.0, 10.0, 0.0, 5.0), box_p1, box_p2)


def test_grazing_face_with_float_noise_is_outside():
    # Same grazing face, but the kernel-noise variant: the face's top edge
    # overshoots the box edge by ~1e-13. Strict comparisons would flip this
    # to "inside"; the tolerance margin must not.
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert not is_face_inside_box(_quad_x(2.0, 0.0, 10.0, 0.0, 5.0000000000001), box_p1, box_p2)


def test_thin_sliver_inside_planar_box_is_inside():
    # A sub-millimetre sliver well inside the region still counts.
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert is_face_inside_box(_quad_x(2.0, 4.0, 6.0, 8.0, 8.0005), box_p1, box_p2)


def test_face_in_wrong_plane_is_outside():
    box_p1, box_p2 = (2.0, 0.0, 5.0), (2.0, 10.0, 12.0)
    assert not is_face_inside_box(_quad_x(3.0, 0.0, 10.0, 6.0, 11.0), box_p1, box_p2)


def test_3d_box_grazing_face_is_outside():
    # Volumetric box; a face coplanar with z=1 below it touches only the
    # bottom edge of the box's side -> outside.
    box_p1, box_p2 = (0.0, 0.0, 1.0), (2.0, 2.0, 3.0)
    face = np.array([[0.0, 0.0, 1.0], [2.0, 0.0, 1.0], [2.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    assert not is_face_inside_box(face, box_p1, box_p2)


def test_3d_box_face_on_surface_is_inside():
    # A face lying on (and overlapping) a box surface counts as inside.
    box_p1, box_p2 = (0.0, 0.0, 1.0), (2.0, 2.0, 3.0)
    face = np.array([[0.0, 0.0, 1.5], [2.0, 0.0, 1.5], [2.0, 0.0, 2.5], [0.0, 0.0, 2.5]])
    assert is_face_inside_box(face, box_p1, box_p2)


def test_side_box_does_not_capture_stacked_cell_below():
    """Regression: get_faces_intersecting_box over a cell's side region.

    Two stacked cells share the z=1 plane. Querying the x=2 side region of
    the UPPER cell must return only the upper cell's side face — the lower
    cell's coplanar side face touches the region along the shared edge only.
    """
    cells = []
    for name, p1, p2 in [("lower", (0, 0, 0), (2, 2, 1)), ("upper", (0, 0, 1), (2, 2, 2))]:
        sp = TopoSpace(NAME=name, X=p1[0], Y=p1[1], Z=p1[2], DX=p2[0] - p1[0], DY=p2[1] - p1[1], DZ=p2[2] - p1[2])
        cells.append((ada.PrimBox(name, p1, p2).solid_occ(), sp))
    cg = CellGraph.from_cell_solids(cells)

    # The x=2 side region of the upper cell.
    faces = cg.get_faces_intersecting_box((2.0, 0.0, 1.0), (2.0, 2.0, 2.0))
    assert [f.parent_cell.name for f in faces] == ["upper"]
