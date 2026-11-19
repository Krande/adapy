from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import (
    create_axis,
    create_property_set,
    write_elem_property_sets,
)
from ada.cadit.ifc.write.shapes.prim_extrude_area import generate_ifc_prim_extrude_geom
from ada.cadit.ifc.write.shapes.prim_sweep_area import generate_ifc_prim_sweep_geom

if TYPE_CHECKING:
    import ifcopenshell

    from ada.api.fasteners import Weld


# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/IfcFastener.htm
# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/Pset_FastenerWeld.htm


def _weld_axis_points(weld: Weld) -> list | None:
    """Reference-curve points for the weld's Axis representation.

    Linear welds carry explicit ``p1``/``p2`` endpoints; swept welds
    leave those ``None`` and instead define their path via
    ``sweep_curve`` (see ``ada.api.fasteners.Weld``). Follow the full
    curve so the axis matches a curved bead, not just its chord.
    Returns ``None`` when no path is available.
    """
    if weld.p1 is not None and weld.p2 is not None:
        return [weld.p1.p, weld.p2.p]

    pts = getattr(weld.sweep_curve, "points3d", None)
    if pts:
        return list(pts)
    return None


def _solid_body(weld: Weld, geometry, f) -> ifcopenshell.entity_instance:
    """The Body representation of a weld whose geometry is a solid rather than a swept profile: the solid's own
    IFC form where it has one (an IfcFacetedBrep for a faceted bead, ...), else the solid tessellated."""
    from ada.base.units import Units
    from ada.cadit.ifc.utils import add_tesselated_shape
    from ada.cadit.ifc.write.write_shapes import parametric_body_representation
    from ada.config import logger

    try:
        return parametric_body_representation(geometry, f)
    except (NotImplementedError, AttributeError) as exc:
        logger.debug(f"ifc-write: the bead of weld {weld.name} has no parametric IFC form ({exc}); tessellating it")
    tol = Units.get_general_point_tol(weld.parent.get_assembly().units)
    return add_tesselated_shape(f, geometry.solid_occ(), tol).Representations[0]


def write_ifc_fastener(weld: Weld) -> ifcopenshell.entity_instance:
    if weld.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = weld.parent.get_assembly()
    ifc_store = a.ifc_store
    f = a.ifc_store.f

    # Body geometry: dispatch on the wrapped primitive. A linear weld
    # wraps a PrimExtrude (SweptSolid); a swept weld a PrimSweep
    # (AdvancedSweptSolid). Mirrors the maps in write_shapes.py. Any other
    # geometry is a solid given as the bead (``Weld(bead=...)`` or a subclass
    # whose ``geometry`` is a Shape): written as that solid.
    from ada import PrimExtrude, PrimSweep

    geometry = weld.geometry
    placement = None
    if isinstance(geometry, PrimSweep):
        geom = generate_ifc_prim_sweep_geom(geometry, f)
        body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "AdvancedSweptSolid", [geom])
    elif isinstance(geometry, PrimExtrude):
        geom = generate_ifc_prim_extrude_geom(geometry, f)
        body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SweptSolid", [geom])
    else:
        body = _solid_body(weld, geometry, f)
        # The solid is in its shape's local system: put it in the world (the swept bodies are written in it).
        place = getattr(geometry, "placement", None)
        if place is not None and not place.is_identity(use_absolute_placement=True):
            from ada.cadit.ifc.utils import create_absolute_placement

            placement = create_absolute_placement(f, geometry)

    representations = [body]
    axis_points = _weld_axis_points(weld)
    if axis_points is not None:
        axis = create_axis(f, axis_points, ifc_store.get_context("Axis"))
        representations.insert(0, axis)

    shape = f.create_entity("IfcProductDefinitionShape", Name=None, Description=None, Representations=representations)

    ifc_fastener = f.create_entity(
        "IfcFastener",
        GlobalId=weld.guid,
        OwnerHistory=ifc_store.owner_history,
        Name=weld.name,
        Description=None,
        ObjectPlacement=placement,
        Representation=shape,
        PredefinedType="WELD",
    )

    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/Pset_FastenerWeld.htm
    create_property_set("Pset_FastenerWeld", f, dict(Type1=weld.type.value), ifc_store.owner_history)

    # adapy reconstruction params (geometry/Pset can't carry them): the weld profile + xdir.
    # p1/p2 come from the Axis; weld_type from Pset_FastenerWeld. Members/groove are not
    # round-tripped (the bead geometry is rebuilt from profile + p1/p2 + xdir).
    write_elem_property_sets(
        {
            "weld_type": weld.type.value,
            "xdir": json.dumps([float(c) for c in weld.xdir]),
            "profile": json.dumps([[float(c) for c in pt] for pt in weld.profile]),
            # Member GUIDs are resolved back to the welded elements in a post-import pass
            # (members may be imported after the weld).
            "members": json.dumps([m.guid for m in weld.members]),
        },
        ifc_fastener,
        f,
        ifc_store.owner_history,
    )

    return ifc_fastener
