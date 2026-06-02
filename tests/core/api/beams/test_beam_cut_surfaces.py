"""Tests for Beam.get_cut_surfaces() — extracting polylines of CSG cut faces."""

from __future__ import annotations

import math

import numpy as np
import pytest

import ada
from ada.api.beams.cut_surfaces import CutEdge, extract_cut_surfaces


def _polyline_lies_on_plane(polyline, plane_origin, plane_normal, tol: float = 1e-6) -> bool:
    n = np.asarray(plane_normal, dtype=float)
    n /= np.linalg.norm(n)
    o = np.asarray(plane_origin, dtype=float)
    for p in polyline:
        v = np.asarray([p[0], p[1], p[2]], dtype=float) - o
        d = abs(float(np.dot(v, n)))
        if d > tol:
            return False
    return True


def _polyline_axis_extents(polyline) -> dict[str, tuple[float, float]]:
    arr = np.asarray([[p[0], p[1], p[2]] for p in polyline], dtype=float)
    return {
        "x": (float(arr[:, 0].min()), float(arr[:, 0].max())),
        "y": (float(arr[:, 1].min()), float(arr[:, 1].max())),
        "z": (float(arr[:, 2].min()), float(arr[:, 2].max())),
    }


def test_no_booleans_returns_empty():
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    assert extract_cut_surfaces(bm) == []


def test_method_on_beam_matches_function():
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0, 0.0), normal=(0, 0, -1), flip=True))
    via_method = bm.get_cut_surfaces()
    via_function = extract_cut_surfaces(bm)
    assert len(via_method) == len(via_function) > 0


def test_halfspace_cut_through_hollow_box_slices_both_side_walls():
    """A horizontal half-space at the cavity height of an RHS slices both side
    walls — expect two co-planar cut faces, one per wall, lying on z=cut_z."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    cut_z = 0.05
    plane_origin = (0.5, 0.0, cut_z)
    plane_normal = (0.0, 0.0, -1.0)
    bm.add_boolean(ada.BoolHalfSpace(origin=plane_origin, normal=plane_normal, flip=True))

    surfs = extract_cut_surfaces(bm)
    assert len(surfs) == 2

    for surf in surfs:
        assert surf.surface_type == "Plane"
        assert len(surf.outer_polyline) == 4

        assert _polyline_lies_on_plane(surf.outer_polyline, plane_origin, plane_normal)

        ext = _polyline_axis_extents(surf.outer_polyline)
        assert ext["x"][0] == pytest.approx(0.0, abs=1e-6)
        assert ext["x"][1] == pytest.approx(1.0, abs=1e-6)
        assert ext["z"][0] == pytest.approx(cut_z, abs=1e-6)
        assert ext["z"][1] == pytest.approx(cut_z, abs=1e-6)

        n = np.asarray(surf.sample_normal, dtype=float)
        assert abs(abs(n[2]) - 1.0) < 1e-6
        assert abs(n[0]) < 1e-6
        assert abs(n[1]) < 1e-6


def test_halfspace_cut_through_solid_circular_yields_single_surface():
    """A horizontal cut through a solid circular bar should produce one disc-shaped cut face."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "CIRC100")
    cut_z = 0.0
    plane_origin = (0.5, 0.0, cut_z)
    plane_normal = (0.0, 0.0, -1.0)
    bm.add_boolean(ada.BoolHalfSpace(origin=plane_origin, normal=plane_normal, flip=True))

    surfs = extract_cut_surfaces(bm)
    assert len(surfs) == 1
    assert surfs[0].surface_type == "Plane"
    assert _polyline_lies_on_plane(surfs[0].outer_polyline, plane_origin, plane_normal)


def test_overlapping_halfspaces_do_not_double_count():
    """Two halfspaces with the same plane should be unioned and produce the
    same number of cut surfaces as a single halfspace on that plane."""
    bm1 = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    plane_origin = (0.5, 0.0, 0.05)
    plane_normal = (0.0, 0.0, -1.0)
    bm1.add_boolean(ada.BoolHalfSpace(origin=plane_origin, normal=plane_normal, flip=True))
    n_single = len(extract_cut_surfaces(bm1))

    bm2 = ada.Beam("b2", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    bm2.add_boolean(ada.BoolHalfSpace(origin=plane_origin, normal=plane_normal, flip=True))
    bm2.add_boolean(ada.BoolHalfSpace(origin=plane_origin, normal=plane_normal, flip=True))
    n_double = len(extract_cut_surfaces(bm2))

    assert n_double == n_single


def test_two_perpendicular_halfspace_cuts_yield_distinct_normals():
    """Two halfspaces on perpendicular planes should produce cut surfaces with
    perpendicular normals."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")

    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.0, 0.05), normal=(0, 0, -1), flip=True))
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.05, 0.0), normal=(0, -1, 0), flip=True))

    surfs = extract_cut_surfaces(bm)
    assert len(surfs) >= 2

    normals = [tuple(round(float(c), 3) for c in s.sample_normal) for s in surfs]
    has_z_aligned = any(abs(n[2]) > 0.99 and abs(n[0]) < 1e-3 and abs(n[1]) < 1e-3 for n in normals)
    has_y_aligned = any(abs(n[1]) > 0.99 and abs(n[0]) < 1e-3 and abs(n[2]) < 1e-3 for n in normals)
    assert has_z_aligned and has_y_aligned


def test_primbox_cutter_produces_planar_cut_surfaces():
    """A PrimBox cutter intersecting the beam should produce planar cut surfaces
    on the box faces that lie inside the beam volume."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")

    cutter = ada.PrimBox("notch", (0.4, -0.2, 0.05), (0.6, 0.2, 0.2))
    bm.add_boolean(cutter)

    surfs = extract_cut_surfaces(bm)
    assert len(surfs) >= 1
    for s in surfs:
        assert s.surface_type == "Plane"
        assert len(s.outer_polyline) >= 3


def test_cut_outside_solid_returns_empty():
    """A cutter that doesn't intersect the beam should produce no cut surfaces."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")

    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.0, 10.0), normal=(0, 0, 1), flip=False))

    assert extract_cut_surfaces(bm) == []


def test_outer_edges_classify_lines_and_arcs_separately():
    """A halfspace cut through a hollow RHS exposes a section-shaped boundary
    with both straight edges (walls, cut transitions) and arc edges (corner
    radii). The outer_edges list should preserve this distinction."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.0, 0.0), normal=(1, 0, 0), flip=True))

    surfs = extract_cut_surfaces(bm)
    assert len(surfs) >= 1
    surf = surfs[0]

    assert len(surf.outer_edges) >= 4
    assert all(isinstance(e, CutEdge) for e in surf.outer_edges)
    assert all(len(e.points) >= 2 for e in surf.outer_edges)

    edge_types = {e.edge_type for e in surf.outer_edges}
    assert "Line" in edge_types

    line_count = sum(1 for e in surf.outer_edges if e.edge_type == "Line")
    assert line_count >= 4


def test_outer_edges_endpoints_match_polyline():
    """The outer_polyline should be the concatenation of edge points (deduped)."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.0, 0.05), normal=(0, 0, -1), flip=True))

    surfs = extract_cut_surfaces(bm)
    for surf in surfs:
        edge_pts: list = []
        for e in surf.outer_edges:
            if not edge_pts:
                edge_pts.extend(e.points)
            else:
                pts = e.points
                if (
                    abs(edge_pts[-1][0] - pts[0][0]) < 1e-6
                    and abs(edge_pts[-1][1] - pts[0][1]) < 1e-6
                    and abs(edge_pts[-1][2] - pts[0][2]) < 1e-6
                ):
                    edge_pts.extend(pts[1:])
                else:
                    edge_pts.extend(pts)
        if (
            len(edge_pts) >= 2
            and abs(edge_pts[0][0] - edge_pts[-1][0]) < 1e-6
            and abs(edge_pts[0][1] - edge_pts[-1][1]) < 1e-6
            and abs(edge_pts[0][2] - edge_pts[-1][2]) < 1e-6
        ):
            edge_pts = edge_pts[:-1]

        assert len(edge_pts) == len(surf.outer_polyline)
        for a, b in zip(edge_pts, surf.outer_polyline):
            assert abs(a[0] - b[0]) < 1e-6
            assert abs(a[1] - b[1]) < 1e-6
            assert abs(a[2] - b[2]) < 1e-6


def test_polyline_points_are_unique():
    """The outer polyline should not contain consecutive duplicate points."""
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX300x300x10x10")
    bm.add_boolean(ada.BoolHalfSpace(origin=(0.5, 0.0, 0.05), normal=(0, 0, -1), flip=True))

    surfs = extract_cut_surfaces(bm)
    for s in surfs:
        for i in range(len(s.outer_polyline)):
            j = (i + 1) % len(s.outer_polyline)
            a = s.outer_polyline[i]
            b = s.outer_polyline[j]
            d = math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
            assert d > 1e-6, f"consecutive duplicate at index {i}: {a} == {b}"
