"""Runaway-face guard in the shell builder (``_add_cfs_faces_to_shell``).

A corrupt trim (a bad edge/p-curve) can make OCC evaluate a surface far from its
topological vertices, so BRepMesh blows the face up into a metres-wide phantom
"disk" that wrecks the model's bounding box — observed on real CAD where 15 cm
solids built out to ~18 m (ratio ~126x). The guard drops any built face whose
extent dwarfs the WHOLE solid's vertex extent, while leaving legit faces (even
closed cylinder/cone/torus faces, bounded by the solid's radius) untouched.
"""

from __future__ import annotations

import math
from types import SimpleNamespace as NS

import pytest

import ada
from ada.cadit.step.read.stream_reader import stream_read_step


def _face(*edge_endpoints):
    """A minimal AdvancedFace-shaped object: bounds -> bound -> edge_list -> start/end."""
    edges = [NS(start=s, end=e) for s, e in edge_endpoints]
    return NS(bounds=[NS(bound=NS(edge_list=edges))])


def test_cfs_vertex_diag_spans_all_faces():
    from ada.occ.geom.surfaces import _cfs_vertex_diag

    faces = [_face(((0, 0, 0), (1, 0, 0))), _face(((0, 0, 0), (0, 2, 0)))]
    # combined extent (0,0,0)..(1,2,0) -> diagonal sqrt(1 + 4)
    assert _cfs_vertex_diag(faces) == pytest.approx(math.sqrt(5))


def test_cfs_vertex_diag_empty_is_zero():
    from ada.occ.geom.surfaces import _cfs_vertex_diag

    assert _cfs_vertex_diag([]) == 0.0
    assert _cfs_vertex_diag([_face()]) == 0.0  # no edges


def test_clean_box_drops_no_faces(tmp_path):
    pytest.importorskip("OCC.Core.BRepBuilderAPI")
    from ada.occ.geom.surfaces import PARAM_REBUILD_STATS, make_closed_shell_from_geom

    out = tmp_path / "box.step"
    (ada.Assembly("m") / (ada.Part("p") / ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)))).to_stp(out)

    before = PARAM_REBUILD_STATS["runaway_face_dropped"]
    for g in stream_read_step(out, local_pool=False, tolerant=True):
        make_closed_shell_from_geom(g.geometry)
    # a clean box has no corrupt faces -> the guard must leave them all
    assert PARAM_REBUILD_STATS["runaway_face_dropped"] == before


def test_guard_drops_oversized_faces(tmp_path, monkeypatch):
    pytest.importorskip("OCC.Core.BRepBuilderAPI")
    import ada.occ.geom.surfaces as S

    out = tmp_path / "box.step"
    (ada.Assembly("m") / (ada.Part("p") / ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)))).to_stp(out)

    # Force every built face to report a runaway extent; the solid's vertex extent
    # (from _cfs_vertex_diag) stays small, so the guard must drop them.
    monkeypatch.setattr(S, "_shape_diag", lambda shape: 1.0e9)
    before = S.PARAM_REBUILD_STATS["runaway_face_dropped"]
    for g in stream_read_step(out, local_pool=False, tolerant=True):
        S.make_closed_shell_from_geom(g.geometry)
    assert S.PARAM_REBUILD_STATS["runaway_face_dropped"] >= before + 6  # 6 box faces
