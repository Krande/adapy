import ifcopenshell

from ada.cadit.ifc.read.geom.surfaces import bspline_surface_with_knots as read_bspline
from ada.cadit.ifc.write.geom.surfaces import bspline_surface_with_knots as write_bspline
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.points import Point


def _rational_surface() -> geo_su.RationalBSplineSurfaceWithKnots:
    # Minimal bilinear (degree 1x1) 2x2 patch with non-uniform weights.
    cps = [[Point(0, 0, 0), Point(0, 1, 0)], [Point(1, 0, 0), Point(1, 1, 1)]]
    return geo_su.RationalBSplineSurfaceWithKnots(
        u_degree=1,
        v_degree=1,
        control_points_list=cps,
        surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[2, 2],
        v_multiplicities=[2, 2],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=[[1.0, 0.5], [0.5, 2.0]],
    )


def test_rational_bspline_surface_preserves_weights():
    surf = _rational_surface()
    f = ifcopenshell.file(schema="IFC4")
    ifc_surf = write_bspline(surf, f)
    assert ifc_surf.is_a("IfcRationalBSplineSurfaceWithKnots")

    back = read_bspline(ifc_surf)
    # Previously the reader downcast this to a non-rational surface, dropping the weights.
    assert isinstance(back, geo_su.RationalBSplineSurfaceWithKnots)
    assert back.weights_data == [[1.0, 0.5], [0.5, 2.0]]


def test_non_rational_bspline_surface_stays_non_rational():
    surf = _rational_surface()
    plain = geo_su.BSplineSurfaceWithKnots(
        u_degree=surf.u_degree,
        v_degree=surf.v_degree,
        control_points_list=surf.control_points_list,
        surface_form=surf.surface_form,
        u_closed=surf.u_closed,
        v_closed=surf.v_closed,
        self_intersect=surf.self_intersect,
        u_multiplicities=surf.u_multiplicities,
        v_multiplicities=surf.v_multiplicities,
        u_knots=surf.u_knots,
        v_knots=surf.v_knots,
        knot_spec=surf.knot_spec,
    )
    f = ifcopenshell.file(schema="IFC4")
    back = read_bspline(write_bspline(plain, f))
    assert isinstance(back, geo_su.BSplineSurfaceWithKnots)
    assert not isinstance(back, geo_su.RationalBSplineSurfaceWithKnots)
