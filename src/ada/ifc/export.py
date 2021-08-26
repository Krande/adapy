import logging

from ada.concepts.levels import Assembly, Part
from ada.config import Settings
from ada.fem.io.ifc.writer import to_ifc_fem

from .utils import create_guid


def add_part_objects_to_ifc(p: Part, f, assembly: Assembly):
    # TODO: Consider having all of these operations happen upon import of elements as opposed to one big operation
    #  on export

    part_ifc = p.ifc_elem
    owner_history = assembly.user.to_ifc()
    physical_objects = []
    for m in p.materials.dmap.values():
        f.add(m.ifc_mat)

    for bm in p.beams:
        f.add(bm.ifc_elem)
        physical_objects.append(bm.ifc_elem)

    for pl in p.plates:
        f.add(pl.ifc_elem)
        physical_objects.append(pl.ifc_elem)

    for pi in p.pipes:
        logging.debug(f'Creating IFC Elem for PIPE "{pi.name}"')
        f.add(pi.ifc_elem)

    for wall in p.walls:
        f.add(wall.ifc_elem)
        physical_objects.append(wall.ifc_elem)

    for shp in p.shapes:
        if "ifc_file" in shp.metadata.keys():
            ifc_file = shp.metadata["ifc_file"]
            ifc_f = assembly.get_ifc_source_by_name(ifc_file)
            ifc_elem = ifc_f.by_guid(shp.guid)
            f.add(ifc_elem)
            physical_objects.append(ifc_elem)
        else:
            f.add(shp.ifc_elem)
            physical_objects.append(shp.ifc_elem)

    if len(p.fem.nodes) > 0:
        if Settings.ifc_include_fem is True:
            to_ifc_fem(p.fem, f)

    if len(physical_objects) == 0:
        return

    f.createIfcRelContainedInSpatialStructure(
        create_guid(),
        owner_history,
        "Physical model",
        None,
        physical_objects,
        part_ifc,
    )
