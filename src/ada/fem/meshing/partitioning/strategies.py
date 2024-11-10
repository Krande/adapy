from typing import TYPE_CHECKING

from ada import Beam, Plate, Section
from ada.fem.meshing.partitioning.partition_beam_interiors import ibeam

if TYPE_CHECKING:
    from ada.fem.meshing.concepts import GmshData, GmshSession


def partition_objects_with_holes(gmsh_data: "GmshData", gmsh_session: "GmshSession"):
    obj = gmsh_data.obj

    partition_map = {Plate: partition_plate_with_hole, Beam: partition_beam_with_hole}
    partition_tool = partition_map.get(type(obj), None)

    if partition_tool is None:
        raise NotImplementedError(f'Partitioning of "{type(obj)}" is not yet supported')

    partition_tool(gmsh_data, gmsh_session)


def partition_solid_beams(gmsh_data: "GmshData", gmsh_session: "GmshSession"):
    obj: Beam = gmsh_data.obj

    partition_map = {Section.TYPES.IPROFILE: ibeam}

    partition_tool = partition_map.get(obj.section.type, None)

    if partition_tool is None:
        raise NotImplementedError(f'Partitioning of "{obj.section.type}" Beams is not yet supported')

    partition_tool(gmsh_data, gmsh_session)


def partition_plate_with_hole(model: "GmshData", gmsh_session: "GmshSession"):
    gmsh_session.model.mesh.recombine()
    for dim, tag in model.entities:
        # ents.append(tag)
        # self.model.mesh.set_transfinite_surface(tag)
        gmsh_session.model.mesh.setRecombine(dim, tag)

    # gmsh_session.open_gui()
    #
    # raise NotImplementedError()


def partition_beam_with_hole(model: "GmshData", gmsh_session: "GmshSession"):
    gmsh_session.model.mesh.recombine()
    for dim, tag in model.entities:
        gmsh_session.model.mesh.setRecombine(dim, tag)

    # gmsh_session.open_gui()
