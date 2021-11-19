import logging

import numpy as np

from ada import Assembly, Beam
from ada.core.vector_utils import unit_vector

from ..concepts import IfcRef
from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import get_associated_material, get_name, getIfcPropertySets


def import_ifc_beam(ifc_elem, ifc_ref: IfcRef, assembly: Assembly = None) -> Beam:
    name = get_name(ifc_elem)
    logging.info(f"importing {name}")
    props = getIfcPropertySets(ifc_elem)
    ass = get_associated_material(ifc_elem)
    sec = None
    mat = None

    if assembly is not None:
        sec = assembly.get_by_name(ass.Profile.ProfileName)
        mat = assembly.get_by_name(ass.Material.Name)

    if sec is None:
        sec = import_section_from_ifc(ass.Profile)

    if mat is None:
        mat = read_material(ass, ifc_ref)

    axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]

    if len(axes) != 1:
        raise ValueError("Number of axis objects attached to element is not 1")
    if len(axes[0].Items) != 1:
        raise ValueError("Number of items objects attached to axis is not 1")

    axis = axes[0].Items[0]
    if axis.is_a("IfcPolyline") and len(axis.Points) != 2:
        raise NotImplementedError("Reading beams swept along IfcPolyLines of length > 2 is not yet supported")
    elif axis.is_a("IfcTrimmedCurve"):
        raise NotImplementedError("Reading beams swept along IfcTrimmedCurve is not yet supported")

    p1 = axis.Points[0].Coordinates
    p2 = axis.Points[1].Coordinates

    yvec = ifc_elem.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios
    xvec = unit_vector(np.array(p2) - np.array(p1))
    zvec = np.cross(xvec, yvec)

    return Beam(name, p1, p2, sec, mat, up=zvec, guid=ifc_elem.GlobalId, metadata=props, ifc_ref=ifc_ref)


def get_beam_geom(ifc_elem, ifc_settings):
    # from .read_shapes import get_ifc_geometry
    # pdct_shape, colour, alpha = get_ifc_geometry(ifc_elem, ifc_settings)

    bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
    if len(bodies) != 1:
        raise ValueError("Number of body objects attached to element is not 1")
    if len(bodies[0].Items) != 1:
        raise ValueError("Number of items objects attached to body is not 1")

    body = bodies[0].Items[0]
    if len(body.StyledByItem) > 0:
        style = body.StyledByItem[0].Styles[0].Styles[0].Styles[0]
        colour = (int(style.SurfaceColour.Red), int(style.SurfaceColour.Green), int(style.SurfaceColour.Blue))
        print(colour)
