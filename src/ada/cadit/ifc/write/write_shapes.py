from __future__ import annotations

from typing import TYPE_CHECKING

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada import (
    Boolean,
    MassPoint,
    PrimBox,
    PrimCone,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.base.units import Units
from ada.cadit.ifc.utils import add_colour, create_local_placement, tesselate_shape
from ada.cadit.ifc.write.geom.curves import indexed_poly_curve, poly_line
from ada.cadit.ifc.write.geom.surfaces import (
    advanced_face,
    create_closed_shell,
    create_half_space_geom,
    curve_bounded_plane,
)
from ada.cadit.ifc.write.shapes.box import generate_ifc_box_geom
from ada.cadit.ifc.write.shapes.cone import generate_ifc_cone_geom
from ada.cadit.ifc.write.shapes.cylinder import generate_ifc_cylinder_geom
from ada.cadit.ifc.write.shapes.prim_extrude_area import generate_ifc_prim_extrude_geom
from ada.cadit.ifc.write.shapes.prim_revolve_area_solid import (
    generate_ifc_prim_revolve_geom,
)
from ada.cadit.ifc.write.shapes.prim_sweep_area import generate_ifc_prim_sweep_geom
from ada.cadit.ifc.write.shapes.sphere import generate_ifc_prim_sphere_geom

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore

from ada.config import logger


def _is_raw_occ_shape(shape) -> bool:
    """STEP / SAT imports produce ``Shape`` instances whose only
    geometry is an OCC ``TopoDS_Shape`` sitting in the transient
    ``_occ_cache`` slot — there's no ``ada.geom.Geometry`` wrapper
    to drive the parametric IFC path. Tesselation handles them
    cleanly. Returns True for those.

    Done as a local helper without importing OCC at module load
    so non-OCC builds (Pyodide / docs-only env) don't carry a
    hard import on every IFC write.
    """
    if getattr(shape, "_geom", None) is not None:
        return False
    return getattr(shape, "_occ_cache", None) is not None


def _default_relative_placement(f):
    """
    Pick a stable placement anchor already in the IFC file.
    Prefer Storey -> Building -> Site -> Project.
    """
    for t in ("IfcBuildingStorey", "IfcBuilding", "IfcSite", "IfcProject"):
        elems = f.by_type(t)
        if elems:
            # These should have ObjectPlacement for spatial structure
            pl = getattr(elems[0], "ObjectPlacement", None)
            if pl is not None:
                return pl
    return None


def update_ifc_shape(ifc_store: IfcStore, shape: Shape):
    logger.warning("Updating IFC shape not implemented yet")


def write_ifc_shape(ifc_store: IfcStore, shape: Shape):
    if shape.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = shape.parent.get_assembly()
    f = a.ifc_store.f
    owner_history = a.ifc_store.owner_history

    parent_ifc = None
    parent_guid = getattr(shape.parent, "guid", None)

    if parent_guid:
        try:
            parent_ifc = f.by_guid(parent_guid)
        except RuntimeError:
            parent_ifc = None

    if parent_ifc is not None and getattr(parent_ifc, "ObjectPlacement", None) is not None:
        rel_to = parent_ifc.ObjectPlacement
    else:
        rel_to = _default_relative_placement(f)
        # If rel_to is still None, create_local_placement should be able to handle it,
        # but if not, you can explicitly create an absolute placement here.

    shape_placement = create_local_placement(f, relative_to=rel_to)

    schema = f.wrapped_data.schema

    # Choose between parametric (round-trippable IfcAdvancedFace /
    # ClosedShell / etc.) and tesselation. The parametric path
    # assumes ``shape.geom`` is an ``ada.geom.Geometry`` wrapper;
    # STEP / SAT imports come in with ``_geom`` as a raw OCC
    # ``TopoDS_Shape`` (no wrapper, no parametric description) and
    # fall straight through to the faceted-brep / triangulated path.
    # Similarly, Shapes constructed without geometry at all
    # (``_geom is None`` but ``solid_occ()`` produces something via
    # ``solid_geom()``) need the parametric path; we only switch to
    # tesselation when there's a concrete OCC body to mesh.
    use_tesselation = _is_raw_occ_shape(shape)
    if not use_tesselation:
        try:
            ifc_shape = generate_parametric_solid(shape, f)
        except (NotImplementedError, AttributeError) as exc:
            # Last-resort fallback for shapes that LOOK parametric
            # (Geometry wrapper present) but whose geometry kind
            # we don't know how to round-trip. Better to ship a
            # faceted brep than fail the whole IFC export — the
            # user still sees the geometry in viewers / queries
            # that don't care about the parametric details.
            logger.warning(
                "ifc-write: parametric path failed for %s (%s); " "falling back to tesselation",
                shape.name,
                exc,
            )
            use_tesselation = True
    if use_tesselation:
        tol = Units.get_general_point_tol(a.units)
        serialized_geom = tesselate_shape(shape.solid_occ(), schema, tol)
        ifc_shape = f.add(serialized_geom)

    # Add colour
    if shape.color is not None:
        color_name = next(ifc_store.writer.color_name_gen)
        add_colour(f, ifc_shape.Representations[0].Items[0], color_name, shape.color)

    from ada.base.ifc_types import ShapeTypes

    # Mark a MassPoint (sphere-bodied) so it reads back as MassPoint, not PrimSphere, and
    # persist its mass (geometry alone can't carry it).
    is_mass_point = isinstance(shape, MassPoint)

    ifc_elem = f.create_entity(
        str(shape.ifc_class.value) if isinstance(shape.ifc_class, ShapeTypes) else shape.ifc_class,
        GlobalId=shape.guid,
        OwnerHistory=owner_history,
        Name=shape.name,
        ObjectType="MassPoint" if is_mass_point else None,
        ObjectPlacement=shape_placement,
        Representation=ifc_shape,
    )

    if is_mass_point:
        from ada.cadit.ifc.utils import write_elem_property_sets

        write_elem_property_sets({"mass": float(shape.mass)}, ifc_elem, f, owner_history)

    return ifc_elem


def _edge_as_polyline(e, f):
    """A bare Edge (line segment) as an IfcPolyline — IfcEdge is a topology
    entity, not a geometric representation item, so it can't go in a
    ShapeRepresentation directly."""
    from ada.cadit.ifc.write.geom.points import cpt

    return f.create_entity("IfcPolyline", Points=[cpt(f, e.start), cpt(f, e.end)])


def generate_parametric_solid(shape: Shape | PrimSphere, f):
    from ada.api.primitives.bool_half_space import BoolHalfSpace

    a = shape.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")

    if isinstance(shape, Boolean):
        raise ValueError(f'Penetration type "{shape}" is not yet supported')

    param_geom_map = {
        PrimSphere: generate_ifc_prim_sphere_geom,
        MassPoint: generate_ifc_prim_sphere_geom,
        PrimBox: generate_ifc_box_geom,
        PrimCyl: generate_ifc_cylinder_geom,
        PrimCone: generate_ifc_cone_geom,
        PrimExtrude: generate_ifc_prim_extrude_geom,
        PrimRevolve: generate_ifc_prim_revolve_geom,
        PrimSweep: generate_ifc_prim_sweep_geom,
        geo_su.AdvancedFace: advanced_face,
        geo_su.CurveBoundedPlane: curve_bounded_plane,
        geo_su.ClosedShell: create_closed_shell,
        # Curve-only bodies (SAT wire bodies import as bare curve geometry):
        # emitted as a Curve3D representation instead of failing solid_occ().
        geo_cu.Edge: _edge_as_polyline,
        geo_cu.IndexedPolyCurve: indexed_poly_curve,
        geo_cu.PolyLine: poly_line,
        # Various
        BoolHalfSpace: create_half_space_geom,
    }

    if type(shape) is Shape:
        param_geo = shape.geom.geometry
    else:
        param_geo = shape

    ifc_geom_converter = param_geom_map.get(type(param_geo), None)
    if ifc_geom_converter is None:
        raise NotImplementedError(f'Shape type "{type(shape)}" is not yet supported for export to IFC')

    solid_geom = ifc_geom_converter(param_geo, f)

    repr_type_map = {
        PrimSphere: "CSG",
        PrimBox: "CSG",
        PrimCyl: "CSG",
        PrimCone: "CSG",
        PrimExtrude: "SweptSolid",
        PrimRevolve: "SweptSolid",
        PrimSweep: "AdvancedSweptSolid",
        geo_su.AdvancedFace: "AdvancedSurface",
        geo_su.CurveBoundedPlane: "AdvancedSurface",
        geo_su.ClosedShell: "AdvancedSurface",
        geo_cu.Edge: "Curve3D",
        geo_cu.IndexedPolyCurve: "Curve3D",
        geo_cu.PolyLine: "Curve3D",
    }
    repr_type_str = repr_type_map.get(type(param_geo), None)
    shape_representation = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType=repr_type_str,
        Items=[solid_geom],
    )
    ifc_shape = f.create_entity(
        "IfcProductDefinitionShape", Name=None, Description=None, Representations=[shape_representation]
    )

    return ifc_shape
