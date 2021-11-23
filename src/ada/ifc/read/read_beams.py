import numpy as np

from ada import Assembly, Beam
from ada.core.vector_utils import calc_yvec, vector_length

from ..concepts import IfcRef
from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import get_associated_material


def import_ifc_beam(ifc_elem, ifc_ref: IfcRef, assembly: Assembly = None) -> Beam:
    name = ifc_elem.Name
    if name is None:
        name = next(assembly.bm_name_gen)

    ass = get_associated_material(ifc_elem)
    sec = None
    mat = None

    if assembly is not None:
        sec = assembly.get_by_name(ass.Profile.ProfileName)
        mat = assembly.get_by_name(ass.Material.Name)

    if sec is None:
        sec = import_section_from_ifc(ass.Profile, units=assembly.units)

    if mat is None:
        mat = read_material(ass, ifc_ref, assembly)

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

    p1_loc = axis.Points[0].Coordinates
    p2_loc = axis.Points[1].Coordinates

    ifc_axis_2_place3d = ifc_elem.ObjectPlacement.RelativePlacement
    origin = ifc_axis_2_place3d.Location.Coordinates

    local_z = np.array(ifc_axis_2_place3d.Axis.DirectionRatios)
    local_x = np.array(ifc_axis_2_place3d.RefDirection.DirectionRatios)
    local_y = calc_yvec(local_x, local_z)

    # res = transform3d([local_x, local_y, local_z], [X, Y], origin, [p1_loc, p2_loc])
    vlen = vector_length(np.array(p2_loc) - np.array(p1_loc))

    p1 = origin
    p2 = np.array(p1) + local_z * vlen

    return Beam(name, p1, p2, sec, mat, up=local_y, guid=ifc_elem.GlobalId, ifc_ref=ifc_ref, units=assembly.units)


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
