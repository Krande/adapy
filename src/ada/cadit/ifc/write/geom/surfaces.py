from __future__ import annotations

import ifcopenshell

from ada import BoolHalfSpace
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

from .curves import (
    circle_curve,
    edge_loop,
    indexed_poly_curve,
    poly_line,
    poly_loop,
    write_curve,
)
from .placement import direction, ifc_placement_from_axis3d, point
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
        elif isinstance(ic, geo_cu.Circle):
            inner_curves.append(circle_curve(ic, f))
        else:
            raise NotImplementedError(f"Unsupported inner curve type: {type(ic)}")

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
    if type(bs) is geo_su.BSplineSurfaceWithKnots:
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
    elif type(bs) is geo_su.RationalBSplineSurfaceWithKnots:
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


def face_bound(fb: geo_su.FaceBound, f: ifcopenshell.file, basis_surface=None) -> ifcopenshell.entity_instance:
    """Converts a FaceBound to an IFC representation"""
    if isinstance(fb.bound, geo_cu.EdgeLoop):
        bound = edge_loop(fb.bound, f, basis_surface=basis_surface)
    elif isinstance(fb.bound, geo_cu.PolyLoop):
        bound = poly_loop(fb.bound, f)
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


def polygonal_face_set(pfs: geo_su.PolygonalFaceSet, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a PolygonalFaceSet to an IfcPolygonalFaceSet.

    Faces reference the shared IfcCartesianPointList3D by 1-based index via
    IfcIndexedPolygonalFace (the sibling of the triangulated face set's CoordIndex)."""
    coordinates = f.create_entity(
        "IfcCartesianPointList3D",
        CoordList=[(float(p.x), float(p.y), float(p.z)) for p in pfs.coordinates],
    )
    faces = [f.create_entity("IfcIndexedPolygonalFace", CoordIndex=[int(i) for i in face]) for face in pfs.faces]
    return f.create_entity(
        "IfcPolygonalFaceSet",
        Coordinates=coordinates,
        Closed=pfs.closed,
        Faces=faces,
    )


def create_closed_shell(cs: geo_su.ClosedShell, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a ClosedShell to an IFC representation.

    ``IfcAdvancedFace`` IS-A ``IfcFaceSurface`` IS-A ``IfcFace`` per
    IFC4x3, so the same ``CfsFaces`` set accepts all three concrete
    types. We already have a writer for AdvancedFace (right below)
    — just wire it in so SAT inputs that decompose to advanced faces
    round-trip through the IFC export instead of failing the whole
    shell.
    """
    faces = []
    for face in cs.cfs_faces:
        if type(face) is geo_su.Face:
            faces.append(create_face(face, f))
        elif type(face) is geo_su.FaceSurface:
            faces.append(create_face_surface(face, f))
        elif type(face) is geo_su.AdvancedFace:
            faces.append(advanced_face(face, f))
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
            # Pass the face's surface so each edge's UV p-curve can be written.
            bounds.append(face_bound(bound, f, basis_surface=face_surface))
        else:
            raise NotImplementedError(f"Unsupported bound type: {type(bound)}")

    return f.create_entity(
        "IfcAdvancedFace",
        Bounds=bounds,
        FaceSurface=face_surface,
        SameSense=af.same_sense,
    )


def create_half_space_geom(bool_half_space: BoolHalfSpace, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Half Space object to Plane to an IFC representation"""
    half_space = bool_half_space.solid_geom()
    plane = half_space.geometry.base_surface

    return f.create_entity(
        "IfcPlane",
        Position=ifc_placement_from_axis3d(plane.position, f),
    )


def create_plane(plane: geo_su.Plane, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Plane to an IFC representation"""

    return f.create_entity(
        "IfcPlane",
        Position=ifc_placement_from_axis3d(plane.position, f),
    )


def create_surface_of_revolution(sor: geo_su.SurfaceOfRevolution, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a SurfaceOfRevolution to an IfcSurfaceOfRevolution.

    The generatrix curve is wrapped in an IfcArbitraryOpenProfileDef (the IFC SweptCurve type)."""
    profile = f.create_entity("IfcArbitraryOpenProfileDef", ProfileType="CURVE", Curve=write_curve(sor.swept_curve, f))
    axis_position = f.create_entity(
        "IfcAxis1Placement", point(sor.axis_position.location, f), direction(sor.axis_position.axis, f)
    )
    position = ifc_placement_from_axis3d(sor.position, f) if sor.position is not None else None
    return f.create_entity("IfcSurfaceOfRevolution", SweptCurve=profile, Position=position, AxisPosition=axis_position)


def create_cylindrical_surface(cs: geo_su.CylindricalSurface, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a CylindricalSurface to an IfcCylindricalSurface."""
    return f.create_entity(
        "IfcCylindricalSurface", Position=ifc_placement_from_axis3d(cs.position, f), Radius=float(cs.radius)
    )


def create_spherical_surface(ss: geo_su.SphericalSurface, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a SphericalSurface to an IfcSphericalSurface."""
    return f.create_entity(
        "IfcSphericalSurface", Position=ifc_placement_from_axis3d(ss.position, f), Radius=float(ss.radius)
    )


def create_toroidal_surface(ts: geo_su.ToroidalSurface, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a ToroidalSurface to an IfcToroidalSurface."""
    return f.create_entity(
        "IfcToroidalSurface",
        Position=ifc_placement_from_axis3d(ts.position, f),
        MajorRadius=float(ts.major_radius),
        MinorRadius=float(ts.minor_radius),
    )


def _bounded_curve(curve: geo_cu.CURVE_GEOM_TYPES, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Write a boundary curve to an IfcCurve (IfcCurveBoundedPlane boundaries are IfcCurve, not
    topological loops)."""
    if isinstance(curve, geo_cu.IndexedPolyCurve):
        return indexed_poly_curve(curve, f)
    elif isinstance(curve, geo_cu.PolyLine):
        return poly_line(curve, f)
    elif isinstance(curve, geo_cu.Circle):
        return circle_curve(curve, f)
    raise NotImplementedError(f"Unsupported curve-bounded-plane boundary type: {type(curve)}")


def curve_bounded_plane(cbp: geo_su.CurveBoundedPlane, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a CurveBoundedPlane to an IfcCurveBoundedPlane.

    IfcCurveBoundedPlane.OuterBoundary / InnerBoundaries are IfcCurve (a planar bounded curve),
    not topological edge loops — the geom ``outer_boundary`` is a CURVE_GEOM_TYPES accordingly."""
    return f.create_entity(
        "IfcCurveBoundedPlane",
        BasisSurface=create_plane(cbp.basis_surface, f),
        OuterBoundary=_bounded_curve(cbp.outer_boundary, f),
        InnerBoundaries=[_bounded_curve(c, f) for c in cbp.inner_boundaries],
    )
