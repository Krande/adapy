from __future__ import annotations

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

from .curves import circle_curve, edge_loop, indexed_poly_curve
from .points import cpt


def arbitrary_profile_def(apd: geo_su.ArbitraryProfileDef, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an ArbitraryProfileDefWithVoids to an IFC representation"""
    if isinstance(apd.outer_curve, geo_cu.IndexedPolyCurve):
        outer_curve = indexed_poly_curve(apd.outer_curve, f)
    elif isinstance(apd.outer_curve, geo_cu.Circle):
        outer_curve = circle_curve(apd.outer_curve, f)
    else:
        raise NotImplementedError(f"Unsupported outer curve type: {type(apd.outer_curve)}")

    inner_curves = []
    for ic in apd.inner_curves:
        if isinstance(ic, geo_cu.IndexedPolyCurve):
            inner_curves.append(indexed_poly_curve(ic, f))

    if len(inner_curves) == 0:
        return f.create_entity(
            "IfcArbitraryClosedProfileDef",
            "AREA",
            ProfileName=apd.profile_name,
            OuterCurve=outer_curve,
        )

    return f.create_entity(
        "IfcArbitraryProfileDefWithVoids",
        "AREA",
        OuterCurve=outer_curve,
        InnerCurves=inner_curves,
        ProfileName=apd.profile_name,
    )


def bspline_surface_with_knots(
    bs: geo_su.BSplineSurfaceWithKnots, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts a BSplineSurfaceWithKnots to an IFC representation"""
    return f.create_entity(
        "IfcBSplineSurfaceWithKnots",
        UDegree=bs.u_degree,
        VDegree=bs.v_degree,
        ControlPointsList=[[cpt(f, p) for p in x] for x in bs.control_points_list],
        SurfaceForm=bs.surface_form.value,
        UClosed=bs.u_closed,
        VClosed=bs.v_closed,
        SelfIntersect=bs.self_intersect,
        UMultiplicities=bs.u_multiplicities,
        VMultiplicities=bs.v_multiplicities,
        UKnots=bs.u_knots,
        VKnots=bs.v_knots,
        KnotSpec=bs.knot_spec.value,
    )


def face_bound(fb: geo_su.FaceBound, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a FaceBound to an IFC representation"""
    if isinstance(fb.bound, geo_cu.EdgeLoop):
        bound = edge_loop(fb.bound, f)
    else:
        raise NotImplementedError(f"Unsupported bound type: {type(fb.bound)}")

    return f.create_entity(
        "IfcFaceBound",
        Bound=bound,
        Orientation=fb.orientation,
    )


def advanced_face(af: geo_su.AdvancedFace, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an AdvancedFace to an IFC representation"""
    if isinstance(af.face_surface, geo_su.BSplineSurfaceWithKnots):
        face_surface = bspline_surface_with_knots(af.face_surface, f)
    else:
        raise NotImplementedError(f"Unsupported face surface type: {type(af.face_surface)}")

    bounds = []
    for bound in af.bounds:
        if isinstance(bound, geo_su.FaceBound):
            bounds.append(face_bound(bound, f))
        else:
            raise NotImplementedError(f"Unsupported bound type: {type(bound)}")

    return f.create_entity(
        "IfcAdvancedFace",
        Bounds=bounds,
        FaceSurface=face_surface,
        SameSense=af.same_sense,
    )
