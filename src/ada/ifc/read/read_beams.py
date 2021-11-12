import numpy as np

from ada import Beam, Material, Section
from ada.core.vector_utils import unit_vector

from .read_shapes import get_ifc_shape
from .reader_utils import get_associated_material


def import_ifc_beam(ifc_elem, name, props, ifc_settings) -> Beam:
    ass = get_associated_material(ifc_elem)
    sec = Section(ass.Profile.ProfileName, ifc_elem=ass.Profile)
    mat = Material(ass.Material.Name, ifc_mat=ass.Material)

    axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]

    if len(axes) != 1:
        raise ValueError("Number of axis objects attached to element is not 1")
    if len(axes[0].Items) != 1:
        raise ValueError("Number of items objects attached to axis is not 1")

    axis = axes[0].Items[0]
    p1 = axis.Points[0].Coordinates
    p2 = axis.Points[1].Coordinates

    yvec = ifc_elem.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios
    xvec = unit_vector(np.array(p2) - np.array(p1))
    zvec = np.cross(xvec, yvec)

    pdct_shape, colour, alpha = get_ifc_shape(ifc_elem, ifc_settings)

    bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
    if len(bodies) != 1:
        raise ValueError("Number of body objects attached to element is not 1")
    if len(bodies[0].Items) != 1:
        raise ValueError("Number of items objects attached to body is not 1")

    body = bodies[0].Items[0]
    if len(body.StyledByItem) > 0:
        style = body.StyledByItem[0].Styles[0].Styles[0].Styles[0]
        colour = (
            int(style.SurfaceColour.Red),
            int(style.SurfaceColour.Green),
            int(style.SurfaceColour.Blue),
        )

    return Beam(
        name,
        p1,
        p2,
        sec,
        mat,
        up=zvec,
        colour=colour,
        opacity=alpha,
        guid=ifc_elem.GlobalId,
        ifc_geom=pdct_shape,
        metadata=props,
    )
