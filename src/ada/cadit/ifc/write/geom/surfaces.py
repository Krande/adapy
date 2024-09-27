from __future__ import annotations

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

from .curves import circle_curve, edge_loop, indexed_poly_curve
from .placement import ifc_placement_from_axis3d
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
    bs: geo_su.BSplineSurfaceWithKnots | geo_su.RationalBSplineSurfaceWithKnots, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts a BSplineSurfaceWithKnots to an IFC representation"""
    if type(bs) == geo_su.BSplineSurfaceWithKnots:
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
    elif type(bs) == geo_su.RationalBSplineSurfaceWithKnots:
        return f.create_entity(
            "IfcRationalBSplineSurfaceWithKnots",
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
            WeightsData=bs.weights_data,
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


def create_connected_face_set(cfs: geo_su.ConnectedFaceSet, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a ConnectedFaceSet to an IFC representation"""
    bounds = []
    for bound in cfs.cfs_faces:
        if isinstance(bound, geo_su.FaceBound):
            bounds.append(face_bound(bound, f))
        else:
            raise NotImplementedError(f"Unsupported bound type: {type(bound)}")

    return f.create_entity(
        "IfcConnectedFaceSet",
        CfsFaces=bounds,
    )


def create_face(face: geo_su.Face, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Face to an IFC representation"""

    bounds = []
    for bound in face.bounds:
        if isinstance(bound, geo_su.FaceBound):
            bounds.append(face_bound(bound, f))
        else:
            raise NotImplementedError(f"Unsupported bound type: {type(bound)}")

    return f.create_entity(
        "IfcFace",
        Bounds=bounds,
    )


def create_face_surface(fs: geo_su.FaceSurface, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a FaceSurface to an IFC representation"""
    if isinstance(fs.face_surface, geo_su.Plane):
        surface = create_plane(fs.face_surface, f)
    else:
        raise NotImplementedError(f"Unsupported surface type: {type(fs.face_surface)}")

    bounds = []
    for bound in fs.bounds:
        if isinstance(bound, geo_su.FaceBound):
            bounds.append(face_bound(bound, f))
        else:
            raise NotImplementedError(f"Unsupported bound type: {type(bound)}")

    return f.create_entity(
        "IfcFaceSurface",
        Bounds=bounds,
        FaceSurface=surface,
        SameSense=fs.same_sense,
    )


def create_closed_shell(cs: geo_su.ClosedShell, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a ClosedShell to an IFC representation"""
    faces = []
    for face in cs.cfs_faces:
        if type(face) is geo_su.Face:
            faces.append(create_face(face, f))
        elif type(face) is geo_su.FaceSurface:
            faces.append(create_face_surface(face, f))
        else:
            raise NotImplementedError(f"Unsupported face type: {type(face)}")

    return f.create_entity(
        "IfcClosedShell",
        CfsFaces=faces,
    )


def advanced_face(af: geo_su.AdvancedFace, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an AdvancedFace to an IFC representation"""
    if type(af.face_surface) in (geo_su.BSplineSurfaceWithKnots, geo_su.RationalBSplineSurfaceWithKnots):
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


def create_plane(plane: geo_su.Plane, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Plane to an IFC representation"""
    return f.create_entity(
        "IfcPlane",
        Position=ifc_placement_from_axis3d(plane.position, f),
    )


def curve_bounded_plane(cbp: geo_su.CurveBoundedPlane, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a CurveBoundedPlane to an IFC representation"""
    basis_surface = create_plane(cbp.basis_surface, f)

    if isinstance(cbp.outer_boundary, geo_cu.EdgeLoop):
        outer_boundary = edge_loop(cbp.outer_boundary, f)
    else:
        raise NotImplementedError(f"Unsupported outer boundary type: {type(cbp.outer_boundary)}")

    if isinstance(cbp.inner_boundaries, list) and len(cbp.inner_boundaries) == 0:
        inner_boundaries = cbp.inner_boundaries
    elif isinstance(cbp.inner_boundaries, geo_cu.EdgeLoop):
        inner_boundaries = edge_loop(cbp.inner_boundaries, f)
    else:
        raise NotImplementedError(f"Unsupported inner boundaries type: {type(cbp.inner_boundaries)}")

    return f.create_entity(
        "IfcCurveBoundedPlane",
        BasisSurface=basis_surface,
        OuterBoundary=outer_boundary,
        InnerBoundaries=inner_boundaries,
    )
