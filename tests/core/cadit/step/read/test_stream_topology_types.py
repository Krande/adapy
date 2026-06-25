"""STEP topology coverage: POLY_LOOP, FACE_SURFACE, CONNECTED_FACE_SET, SUB*.

Each imports into a native ada.geom topology type (no geometry left behind).
"""

from __future__ import annotations

import ada.geom.curves as gc
import ada.geom.surfaces as gs
from ada.cadit.step.read import stream_reader as sr


class _IdResolver:
    def deref(self, x):
        return x


def test_poly_loop_imports():
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
    pl = sr._b_poly_loop(_IdResolver(), ["", pts])
    assert isinstance(pl, gc.PolyLoop) and len(pl.polygon) == 3


def test_face_surface_imports_as_advanced_face():
    # FACE_SURFACE shares ADVANCED_FACE's structure -> full surface support
    fb = gs.FaceBound(bound=gc.EdgeLoop(edge_list=[]), orientation=True)
    f = sr._b_advanced_face(_IdResolver(), ["", [fb], gs.Plane(None), sr._Enum("T")])
    assert isinstance(f, gs.AdvancedFace)


def test_connected_face_set_imports_as_shell():
    cfs = sr._b_connected_face_set(_IdResolver(), ["", []])
    assert isinstance(cfs, gs.OpenShell)


def test_subface_reuses_parent_surface():
    parent = gs.AdvancedFace(bounds=[], face_surface=gs.Plane(None), same_sense=True)
    fb = gs.FaceBound(bound=gc.EdgeLoop(edge_list=[]), orientation=True)
    sub = sr._b_subface(_IdResolver(), ["", [fb], parent])
    assert isinstance(sub, gs.AdvancedFace) and sub.face_surface is parent.face_surface


def test_subedge_reuses_parent_curve():
    parent = gc.EdgeCurve(start=(0, 0, 0), end=(1, 0, 0), edge_geometry=gc.Line((0, 0, 0), (1, 0, 0)), same_sense=True)
    sub = sr._b_subedge(_IdResolver(), ["", (0.2, 0, 0), (0.8, 0, 0), parent])
    assert isinstance(sub, gc.EdgeCurve) and sub.edge_geometry is parent.edge_geometry


def test_topology_types_registered():
    for t in ("POLY_LOOP", "FACE_SURFACE", "CONNECTED_FACE_SET", "SUBFACE", "SUBEDGE"):
        assert t in sr._BUILDERS
