from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Placement, Plate
from ada.config import get_logger

from .read_curves import import_indexedpolycurve, import_polycurve
from .read_materials import read_material
from .reader_utils import get_associated_material

if TYPE_CHECKING:
    from ada.ifc.store import IfcStore

logger = get_logger()


def import_ifc_plate(ifc_elem, name, ifc_store: IfcStore) -> Plate:
    from .exceptions import NoIfcAxesAttachedError

    logger.info(f"importing {name}")
    ifc_mat = get_associated_material(ifc_elem)
    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)

    if mat is None:
        mat = read_material(ifc_mat, ifc_store)

    # TODO: Fix interpretation of IfcIndexedPolyCurve. Should pass origin to get actual 2d coordinates.
    # Adding Axis information
    axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]
    if len(axes) != 1:
        raise NoIfcAxesAttachedError("IfcPlate does not have an Axis representation Item")
    axis = axes[0]
    origin = axis.Items[0].Points[0].Coordinates

    # Adding Body
    bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
    if len(bodies) != 1:
        raise NotImplementedError("Geometry with multiple bodies is not currently supported")
    if len(bodies[0].Items) != 1:
        raise NotImplementedError("Body with multiple Items is not currently supported")

    item = bodies[0].Items[0]
    t = item.Depth
    normal = item.ExtrudedDirection.DirectionRatios
    xdir = item.Position.RefDirection.DirectionRatios
    outer_curve = item.SweptArea.OuterCurve

    if outer_curve.is_a("IfcIndexedPolyCurve"):
        nodes2d = import_indexedpolycurve(outer_curve, normal, xdir, origin)
    else:
        nodes2d = import_polycurve(outer_curve, normal, xdir)

    if nodes2d is None or t is None:
        raise ValueError("Unable to get plate nodes or thickness")

    placement = Placement(origin, xdir=xdir, zdir=normal)

    return Plate(
        name,
        nodes2d,
        t,
        mat=mat,
        placement=placement,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )
