from typing import Iterable

from OCC.Core.BRep import BRep_Tool
from OCC.Core.Geom import Geom_BSplineSurface, Geom_Surface
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face, TopoDS_Shell, topods

from ada.config import logger
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.occ.step.geom.curves import get_wires_from_face
from ada.occ.step.geom.helpers import (
    array1_to_int_list,
    array1_to_list,
    array2_to_point_list,
)

# Assuming occ_geom is your TopoDS_Compound
# Helper functions to convert OCC arrays to Python lists


def occ_face_to_ada_face(face: TopoDS_Face) -> geo_su.AdvancedFace | None:
    surface = BRep_Tool.Surface(face)
    # pythonocc's ``IsKind(Geom_BSplineSurface.get_type_descriptor())``
    # returns True for unrelated surface types (notably Geom_Plane),
    # so we compare the dynamic type's name string directly — that's
    # the reliable identity check across the pythonocc bindings.
    if surface.DynamicType().Name() == "Geom_BSplineSurface":
        edge_loops = get_wires_from_face(face, surface)
        bspline_surf = get_bsplinesurface_with_knots(surface)
        if bspline_surf and edge_loops:
            # Wrap each EdgeLoop in a FaceBound so the AdvancedFace
            # round-trips cleanly through make_face_from_geom — the
            # BSpline-surface OCC builder walks ``face_bound.bound.edge_list``
            # to construct OCC edges in the same order as the source
            # wire, so an EdgeLoop-with-OrientedEdges chain is exactly
            # what it expects.
            bounds = [geo_su.FaceBound(bound=el, orientation=True) for el in edge_loops]
            return geo_su.AdvancedFace(bounds=bounds, face_surface=bspline_surf)
        else:
            raise NotImplementedError("Failed to retrieve B-Spline surface with knots")
    else:
        logger.error(f"Geometry type {surface.__class__} not implemented")
    return None


def occ_shell_to_ada_faces(shell: TopoDS_Shell) -> list[geo_su.AdvancedFace]:
    """
    Convert an OCC TopoDS_Shell to a list of ada AdvancedFace objects.

    Args:
        shell: TopoDS_Shell to convert

    Returns:
        List of AdvancedFace objects extracted from the shell
    """
    faces = []
    explorer = TopExp_Explorer(shell, TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        ada_face = occ_face_to_ada_face(face)
        if ada_face:
            faces.append(ada_face)
        explorer.Next()
    return faces


def iter_faces(occ_geom: TopoDS_Compound) -> Iterable[geo_su.SURFACE_GEOM_TYPES]:
    explorer = TopExp_Explorer(occ_geom, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        yield occ_face_to_ada_face(face)
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

    # KnotType is the knot-vector classification (IfcKnotType / STEP knot_type),
    # not surface continuity. OCC does not expose it directly and the previous
    # C0/C1/C2 strings here were both wrong (that's continuity) and un-enumerated
    # (the IFC/STEP writers do ``knot_spec.value``). UNSPECIFIED is always valid.
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
