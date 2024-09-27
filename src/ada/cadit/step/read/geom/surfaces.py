from typing import Iterable

from OCC.Core.BRep import BRep_Tool
from OCC.Core.Geom import Geom_BSplineSurface, Geom_Surface
from OCC.Core.GeomAbs import GeomAbs_C0, GeomAbs_C1, GeomAbs_C2, GeomAbs_CN
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Compound

from ada.cadit.step.read.geom.curves import get_wires_from_face
from ada.cadit.step.read.geom.helpers import (
    array1_to_int_list,
    array1_to_list,
    array2_to_point_list,
)
from ada.config import logger
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

# Assuming occ_geom is your TopoDS_Compound
# Helper functions to convert OCC arrays to Python lists


def iter_faces(occ_geom: TopoDS_Compound) -> Iterable[geo_su.SURFACE_GEOM_TYPES]:
    explorer = TopExp_Explorer(occ_geom, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        surface = BRep_Tool.Surface(face)
        if surface.IsKind(Geom_BSplineSurface.get_type_descriptor()):
            wires = get_wires_from_face(face, surface)
            bspline_surf = get_bsplinesurface_with_knots(surface)
            if bspline_surf and wires:
                yield geo_su.AdvancedFace(bounds=wires, face_surface=bspline_surf)
            else:
                raise NotImplementedError("Failed to retrieve B-Spline surface with knots")
        else:
            logger.error(f"Geometry type {surface.__class__} not implemented")
        explorer.Next()


# Main function to retrieve the B-Spline surface with knots
def get_bsplinesurface_with_knots(surface: Geom_Surface) -> geo_su.BSplineSurfaceWithKnots | None:
    bspline_surface: Geom_BSplineSurface = Geom_BSplineSurface.DownCast(surface)

    # Extract basic B-Spline parameters
    u_degree = bspline_surface.UDegree()
    v_degree = bspline_surface.VDegree()
    poles = bspline_surface.Poles()
    u_knots = bspline_surface.UKnots()
    v_knots = bspline_surface.VKnots()
    u_multiplicities = bspline_surface.UMultiplicities()
    v_multiplicities = bspline_surface.VMultiplicities()
    u_closed = bspline_surface.IsUClosed()
    v_closed = bspline_surface.IsVClosed()
    # self_intersect = bspline_surface.()
    self_intersect = False  # Placeholder

    # Convert knots, poles, and multiplicities to lists
    u_knots_list = array1_to_list(u_knots)
    v_knots_list = array1_to_list(v_knots)
    poles_list = array2_to_point_list(poles)
    u_multiplicities_list = array1_to_int_list(u_multiplicities)
    v_multiplicities_list = array1_to_int_list(v_multiplicities)

    # Determine knot specification (C0, C1, etc.)
    continuity = bspline_surface.Continuity()
    if continuity == GeomAbs_C0:
        knot_spec = "C0"
    elif continuity == GeomAbs_C1:
        knot_spec = "C1"
    elif continuity == GeomAbs_C2:
        knot_spec = "C2"
    elif continuity == GeomAbs_CN:
        knot_spec = "CN"
    else:
        knot_spec = geo_cu.KnotType.UNSPECIFIED

    # Construct and return the BSplineSurfaceWithKnots object
    return geo_su.BSplineSurfaceWithKnots(
        u_degree=u_degree,
        v_degree=v_degree,
        control_points_list=poles_list,
        surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,  # Placeholder
        u_closed=u_closed,
        v_closed=v_closed,
        self_intersect=self_intersect,
        u_multiplicities=u_multiplicities_list,
        v_multiplicities=v_multiplicities_list,
        u_knots=u_knots_list,
        v_knots=v_knots_list,
        knot_spec=knot_spec,
    )
