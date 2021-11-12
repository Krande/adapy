from ada.concepts.structural import Plate
from ada.concepts.transforms import Placement
from ada.ifc.read.read_shapes import get_ifc_shape

from .read_curves import import_indexedpolycurve, import_polycurve


def import_ifc_plate(ifc_elem, name, props, ifc_settings) -> Plate:
    pdct_shape, color, alpha = get_ifc_shape(ifc_elem, ifc_settings)

    # TODO: Fix interpretation of IfcIndexedPolyCurve. Should pass origin to get actual 2d coordinates.

    # Adding Axis information
    axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]
    if len(axes) != 1:
        raise NotImplementedError("Geometry with multiple axis is not currently supported")
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
        placement=placement,
        guid=ifc_elem.GlobalId,
        colour=color,
        opacity=alpha,
        ifc_geom=pdct_shape,
        metadata=props,
    )
