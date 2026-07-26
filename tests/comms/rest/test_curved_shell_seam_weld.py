"""The converter auto-enables the thick-curved-shell seam-weld (ADA_TESS_WT_CDT_FULL_PATCH)
for models that carry a thickened PlateCurved, and leaves it off otherwise.

The flag routes a thick curved shell's shared near-full cap/wall faces through adacpp's
boundary-first CDT so the per-solid weld closes the cap<->wall seam (a hull strake that
otherwise renders with a visible crack). This test pins the adapy-side wiring only — the
native tessellation is stubbed, so it needs no adacpp build.
"""

from __future__ import annotations

import os

import pytest

import ada
import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.api.plates.base_pl import PlateCurved
from ada.comms.rest import converter as C
from ada.geom import Geometry
from ada.geom.curves import KnotType
from ada.geom.direction import Direction
from ada.geom.points import Point


def _curved_face() -> su.AdvancedFace:
    surf = su.BSplineSurfaceWithKnots(
        u_degree=2,
        v_degree=1,
        control_points_list=[
            [Point(0, 0, 0), Point(0, 1, 0)],
            [Point(0.5, 0, 0.3), Point(0.5, 1, 0.3)],
            [Point(1, 0, 0), Point(1, 1, 0)],
        ],
        surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[3, 3],
        v_multiplicities=[2, 2],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )
    p00, p10, p11, p01 = Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0), Point(0, 1, 0)
    e0 = cu.EdgeCurve(p00, p10, edge_geometry=cu.Line(p00, Direction(1, 0, 0)), same_sense=True)
    e1 = cu.EdgeCurve(p10, p11, edge_geometry=cu.Line(p10, Direction(0, 1, 0)), same_sense=True)
    e2 = cu.EdgeCurve(p01, p11, edge_geometry=cu.Line(p01, Direction(1, 0, 0)), same_sense=True)
    e3 = cu.EdgeCurve(p01, p00, edge_geometry=cu.Line(p01, Direction(0, -1, 0)), same_sense=True)
    loop = cu.EdgeLoop(
        edge_list=[
            cu.OrientedEdge(p00, p10, edge_element=e0, orientation=True),
            cu.OrientedEdge(p10, p11, edge_element=e1, orientation=True),
            cu.OrientedEdge(p11, p01, edge_element=e2, orientation=False),
            cu.OrientedEdge(p01, p00, edge_element=e3, orientation=True),
        ]
    )
    return su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=True)


def _curved_shell_model() -> ada.Assembly:
    pl = PlateCurved("curved", Geometry("curved", _curved_face(), None), t=0.025)
    return ada.Assembly("m") / (ada.Part("p") / pl)


def _flat_plate_model() -> ada.Assembly:
    pl = ada.Plate.from_3d_points("flat", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.02)
    return ada.Assembly("m") / (ada.Part("p") / pl)


def test_detects_thick_curved_shell():
    assert C._model_has_thick_curved_shells(_curved_shell_model()) is True


def test_flat_plate_not_detected():
    assert C._model_has_thick_curved_shells(_flat_plate_model()) is False


@pytest.mark.parametrize("builder, expect_flag", [(_curved_shell_model, "1"), (_flat_plate_model, None)])
def test_converter_scopes_seam_weld_flag(monkeypatch, tmp_path, builder, expect_flag):
    """The native GLB route sees ADA_TESS_WT_CDT_FULL_PATCH set iff the model has a thick
    curved shell, and the ambient env is unchanged after the call (no leak)."""
    monkeypatch.delenv("ADA_TESS_WT_CDT_FULL_PATCH", raising=False)
    out = tmp_path / "out.glb"
    seen = {}

    def _fake_native(model, source_ext, target, out_path, on_progress, *, glb_tess_engine=None):
        seen["flag"] = os.environ.get("ADA_TESS_WT_CDT_FULL_PATCH")
        out_path.write_bytes(b"glTF-stub")
        return out_path  # short-circuits _export_with_ada before to_gltf/adacpp

    monkeypatch.setattr(C, "_native_ngeom_mesh_route", _fake_native)

    result = C._export_with_ada(
        builder(), "glb", out, lambda *_: None, merge_meshes=True, source_ext=".xml", glb_tess_engine="libtess2"
    )
    assert result == out
    assert seen["flag"] == expect_flag
    # restored: the scoped set must not leak into the next job
    assert "ADA_TESS_WT_CDT_FULL_PATCH" not in os.environ
