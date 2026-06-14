import ifcopenshell

from ada.core.utils import flatten
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.points import Point

from .curves import edge_loop, get_curve


def plane(ifc_entity: ifcopenshell.entity_instance) -> geo_su.Plane:
    from .placement import axis3d

    return geo_su.Plane(position=axis3d(ifc_entity.Position))


def half_space_solid(ifc_entity: ifcopenshell.entity_instance) -> geo_su.HalfSpaceSolid:
    """IfcHalfSpaceSolid (and the IfcPolygonalBoundedHalfSpace subtype) -> the unbounded
    HalfSpaceSolid adapy already knows how to cut with (see occ.geom.boolean). The polygonal
    bound of the subtype is dropped: half-spaces only appear here as boolean cut operands,
    where the unbounded plane gives the same trim for the clipped bodies in practice."""
    base = ifc_entity.BaseSurface
    if not base.is_a("IfcPlane"):
        raise NotImplementedError(f"HalfSpaceSolid base surface {base.is_a()} is not implemented")
    return geo_su.HalfSpaceSolid(base_surface=plane(base), agreement_flag=ifc_entity.AgreementFlag)


def get_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.SURFACE_GEOM_TYPES:
    if ifc_entity.is_a("IfcArbitraryProfileDefWithVoids") or ifc_entity.is_a("IfcArbitraryClosedProfileDef"):
        return arbitrary_closed_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcIShapeProfileDef"):
        return i_shape_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcTShapeProfileDef"):
        return t_shape_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcCircleProfileDef"):
        return circle_profile_def(ifc_entity)
    elif ifc_entity.is_a("IfcRectangleProfileDef"):
        return rectangle_profile_def(ifc_entity)
    else:
        raise NotImplementedError(f"Geometry type {ifc_entity.is_a()} not implemented")


def arbitrary_closed_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.ArbitraryProfileDef:
    outer_curve = get_curve(ifc_entity.OuterCurve)

    inner_curves = []
    if hasattr(ifc_entity, "InnerCurves"):
        for inner_curve in ifc_entity.InnerCurves:
            inner_curves.append(get_curve(inner_curve))

    return geo_su.ArbitraryProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        outer_curve=outer_curve,
        inner_curves=inner_curves,
    )


def i_shape_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.IShapeProfileDef:
    return geo_su.IShapeProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        overall_width=ifc_entity.OverallWidth,
        overall_depth=ifc_entity.OverallDepth,
        web_thickness=ifc_entity.WebThickness,
        flange_thickness=ifc_entity.FlangeThickness,
        fillet_radius=ifc_entity.FilletRadius,
        flange_edge_radius=ifc_entity.FlangeEdgeRadius,
        flange_slope=ifc_entity.FlangeSlope,
    )


def t_shape_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.TShapeProfileDef:
    return geo_su.TShapeProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        depth=ifc_entity.Depth,
        flange_width=ifc_entity.FlangeWidth,
        web_thickness=ifc_entity.WebThickness,
        flange_thickness=ifc_entity.FlangeThickness,
        fillet_radius=ifc_entity.FilletRadius,
        flange_edge_radius=ifc_entity.FlangeEdgeRadius,
        web_edge_radius=ifc_entity.WebEdgeRadius,
        web_slope=ifc_entity.WebSlope,
        flange_slope=ifc_entity.FlangeSlope,
    )


def circle_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.CircleProfileDef:
    return geo_su.CircleProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        radius=ifc_entity.Radius,
    )


def triangulated_face_set(ifc_entity: ifcopenshell.entity_instance) -> geo_su.TriangulatedFaceSet:
    return geo_su.TriangulatedFaceSet(
        coordinates=[Point(*x) for x in ifc_entity.Coordinates.CoordList],
        indices=flatten(ifc_entity.CoordIndex),
        normals=[Direction(*x) for x in ifc_entity.Normals],
    )


def polygonal_face_set(ifc_entity: ifcopenshell.entity_instance) -> geo_su.PolygonalFaceSet:
    # Each IfcIndexedPolygonalFace carries 1-based CoordIndex into the shared point list.
    # IfcIndexedPolygonalFaceWithVoids (a subtype) additionally has InnerCoordIndices — not
    # yet represented; its outer loop still reads correctly here.
    return geo_su.PolygonalFaceSet(
        coordinates=[Point(*x) for x in ifc_entity.Coordinates.CoordList],
        faces=[list(face.CoordIndex) for face in ifc_entity.Faces],
        closed=bool(ifc_entity.Closed) if ifc_entity.Closed is not None else True,
    )


def rectangle_profile_def(ifc_entity: ifcopenshell.entity_instance) -> geo_su.RectangleProfileDef:
    return geo_su.RectangleProfileDef(
        profile_type=geo_su.ProfileType.from_str(ifc_entity.ProfileType),
        x_dim=ifc_entity.XDim,
        y_dim=ifc_entity.YDim,
    )


def poly_loop(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.PolyLoop:
    return geo_cu.PolyLoop(polygon=[Point(p.Coordinates) for p in ifc_entity.Polygon])


def face_bound(ifc_entity: ifcopenshell.entity_instance) -> geo_su.FaceBound:

    ifc_bound = ifc_entity.Bound
    if ifc_bound.is_a("IfcEdgeLoop"):
        bound = edge_loop(ifc_bound)
    elif ifc_bound.is_a("IfcPolyLoop"):
        bound = poly_loop(ifc_bound)
    else:
        raise NotImplementedError(f"{ifc_entity} is not yet implemented.")

    return geo_su.FaceBound(
        bound=bound,
        orientation=ifc_entity.Orientation,
    )


def face(ifc_entity: ifcopenshell.entity_instance) -> geo_su.Face:
    """IfcFace -> Face. IfcFaceBound and IfcFaceOuterBound both read as FaceBound."""
    return geo_su.Face(bounds=[face_bound(b) for b in ifc_entity.Bounds])


def bspline_surface_with_knots(
    ifc_entity: ifcopenshell.entity_instance,
) -> geo_su.BSplineSurfaceWithKnots | geo_su.RationalBSplineSurfaceWithKnots:
    kwargs = dict(
        u_degree=ifc_entity.UDegree,
        v_degree=ifc_entity.VDegree,
        control_points_list=[[Point(*p) for p in x] for x in ifc_entity.ControlPointsList],
        surface_form=geo_su.BSplineSurfaceForm.from_str(ifc_entity.SurfaceForm),
        u_closed=ifc_entity.UClosed,
        v_closed=ifc_entity.VClosed,
        self_intersect=ifc_entity.SelfIntersect,
        u_multiplicities=ifc_entity.UMultiplicities,
        v_multiplicities=ifc_entity.VMultiplicities,
        u_knots=ifc_entity.UKnots,
        v_knots=ifc_entity.VKnots,
        knot_spec=geo_cu.KnotType.from_str(ifc_entity.KnotSpec),
    )
    # IfcRationalBSplineSurfaceWithKnots is a subtype of IfcBSplineSurfaceWithKnots — preserve
    # its per-control-point weights instead of silently downcasting to a non-rational surface.
    if ifc_entity.is_a("IfcRationalBSplineSurfaceWithKnots"):
        return geo_su.RationalBSplineSurfaceWithKnots(
            **kwargs, weights_data=[list(row) for row in ifc_entity.WeightsData]
        )
    return geo_su.BSplineSurfaceWithKnots(**kwargs)


def cylindrical_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.CylindricalSurface:
    from .placement import axis3d

    return geo_su.CylindricalSurface(position=axis3d(ifc_entity.Position), radius=ifc_entity.Radius)


def spherical_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.SphericalSurface:
    from .placement import axis3d

    return geo_su.SphericalSurface(position=axis3d(ifc_entity.Position), radius=ifc_entity.Radius)


def toroidal_surface(ifc_entity: ifcopenshell.entity_instance) -> geo_su.ToroidalSurface:
    from .placement import axis3d

    return geo_su.ToroidalSurface(
        position=axis3d(ifc_entity.Position),
        major_radius=ifc_entity.MajorRadius,
        minor_radius=ifc_entity.MinorRadius,
    )


def face_surface_geom(ifc_surface: ifcopenshell.entity_instance) -> geo_su.SURFACE_GEOM_TYPES:
    """Read any supported IfcSurface used as an AdvancedFace.FaceSurface."""
    # IfcRationalBSplineSurfaceWithKnots is a subtype of IfcBSplineSurfaceWithKnots — the one
    # reader handles both and preserves rationality.
    if ifc_surface.is_a("IfcBSplineSurfaceWithKnots"):
        return bspline_surface_with_knots(ifc_surface)
    elif ifc_surface.is_a("IfcCylindricalSurface"):
        return cylindrical_surface(ifc_surface)
    elif ifc_surface.is_a("IfcSphericalSurface"):
        return spherical_surface(ifc_surface)
    elif ifc_surface.is_a("IfcToroidalSurface"):
        return toroidal_surface(ifc_surface)
    elif ifc_surface.is_a("IfcPlane"):
        return plane(ifc_surface)
    raise NotImplementedError(f"Face surface type {ifc_surface.is_a()} is not implemented")


def advanced_face(ifc_entity: ifcopenshell.entity_instance) -> geo_su.AdvancedFace:
    return geo_su.AdvancedFace(
        bounds=[face_bound(x) for x in ifc_entity.Bounds],
        face_surface=face_surface_geom(ifc_entity.FaceSurface),
    )


def closed_shell(ifc_entity: ifcopenshell.entity_instance) -> geo_su.ClosedShell:
    from ada.cadit.ifc.read.geom.geom_reader import import_geometry_from_ifc_geom

    faces = []
    for face in ifc_entity.CfsFaces:
        faces.append(import_geometry_from_ifc_geom(face))
    return geo_su.ClosedShell(faces)


def open_shell(ifc_entity: ifcopenshell.entity_instance) -> geo_su.OpenShell:
    from ada.cadit.ifc.read.geom.geom_reader import import_geometry_from_ifc_geom

    faces = []
    for face in ifc_entity.CfsFaces:
        faces.append(import_geometry_from_ifc_geom(face))
    return geo_su.OpenShell(faces)


def shell_based_surface_model(ifc_entity: ifcopenshell.entity_instance) -> geo_su.ShellBasedSurfaceModel:
    sbsm_boundary = []
    for face in ifc_entity.SbsmBoundary:
        if face.is_a("IfcOpenShell"):
            sbsm_boundary.append(open_shell(face))
        elif face.is_a("IfcClosedShell"):
            sbsm_boundary.append(closed_shell(face))
        else:
            raise NotImplementedError(f"{face} is not yet implemented.")

    return geo_su.ShellBasedSurfaceModel(sbsm_boundary=sbsm_boundary)
