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


def write_ifc_fastener(weld: Weld) -> ifcopenshell.entity_instance:
    if weld.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = weld.parent.get_assembly()
    ifc_store = a.ifc_store
    f = a.ifc_store.f

    # Body geometry: dispatch on the wrapped primitive. A linear weld
    # wraps a PrimExtrude (SweptSolid); a swept weld a PrimSweep
    # (AdvancedSweptSolid). Mirrors the maps in write_shapes.py.
    geometry = weld.geometry
    if type(geometry).__name__ == "PrimSweep":
        geom = generate_ifc_prim_sweep_geom(geometry, f)
        body_repr_type = "AdvancedSweptSolid"
    else:
        geom = generate_ifc_prim_extrude_geom(geometry, f)
        body_repr_type = "SweptSolid"
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", body_repr_type, [geom])

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
