from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada import Plate
from ada.config import logger
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .geom.geom_reader import get_product_definitions
from .read_materials import read_material
from .reader_utils import get_associated_material

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


# Default thickness for curved plates whose IFC carries only a surface (no
# layer-set / thickness). Used purely for the flat-plate render fallback.
_CURVED_PLATE_DEFAULT_T = 0.01


def _is_extruded_arbitrary(geometry) -> bool:
    return isinstance(geometry, geo_so.ExtrudedAreaSolid) and isinstance(
        getattr(geometry, "swept_area", None), geo_su.ArbitraryProfileDef
    )


def _read_plate_material(ifc_elem, name, ifc_store: IfcStore):
    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)
    if mat is None:
        mat = read_material(get_associated_material(ifc_elem), ifc_store)
    return mat


def _edge_loop_points(advanced_face: geo_su.AdvancedFace) -> list[tuple[float, float, float]]:
    """Perimeter (vertex) points of the face's outer bound, for the flat fallback."""
    if not advanced_face.bounds:
        return []
    loop = advanced_face.bounds[0].bound
    edge_list = getattr(loop, "edge_list", None) or []
    return [tuple(float(c) for c in edge.start) for edge in edge_list]


def _import_curved_plate(ifc_elem, name, advanced_face: geo_su.AdvancedFace, ifc_store: IfcStore):
    """Import an ``IfcAdvancedFace`` plate as a :class:`PlateCurved`.

    Mirrors the gxml curved-plate path: keep the B-spline surface as the
    geometry and attach the outer-loop endpoints as ``_flat_fallback_pts`` so the
    tessellator degrades to a flat plate if the trimmed B-spline can't be meshed.
    """
    from ada import PlateCurved
    from ada.geom import Geometry

    from .read_color import get_product_color

    color = get_product_color(ifc_elem, ifc_store.f)
    pc = PlateCurved(
        name,
        Geometry(ifc_elem.GlobalId, advanced_face, color),
        t=_CURVED_PLATE_DEFAULT_T,
        mat=_read_plate_material(ifc_elem, name, ifc_store),
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
    )
    fallback_pts = _edge_loop_points(advanced_face)
    if fallback_pts:
        pc._flat_fallback_pts = fallback_pts
    return pc


def import_ifc_plate(ifc_elem: ifcopenshell.entity_instance, name, ifc_store: IfcStore):
    logger.info(f"importing {name}")
    geometries = get_product_definitions(ifc_elem)

    # A curved (B-spline) plate surface -> PlateCurved (with flat-plate fallback).
    if len(geometries) == 1 and isinstance(geometries[0], geo_su.AdvancedFace):
        return _import_curved_plate(ifc_elem, name, geometries[0], ifc_store)

    # Only an extruded arbitrary profile maps to a parametric Plate. Anything else
    # (e.g. another BREP form) is imported as a generic geometry-backed Shape so it
    # still renders and round-trips.
    if len(geometries) != 1 or not _is_extruded_arbitrary(geometries[0]):
        from .read_shapes import import_ifc_shape

        return import_ifc_shape(ifc_elem, name, ifc_store, force_geom=True)

    body: geo_so.ExtrudedAreaSolid = geometries[0]
    points2d = body.swept_area.outer_curve.to_points2d()
    ifc_mat = get_associated_material(ifc_elem)

    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)

    if mat is None:
        mat = read_material(ifc_mat, ifc_store)

    return Plate(
        name,
        points2d,
        body.depth,
        origin=body.position.location,
        xdir=body.position.ref_direction,
        normal=body.position.axis,
        mat=mat,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )
