import logging

from ada.concepts.levels import Assembly, Part
from ada.fem.formats.ifc.writer import to_ifc_fem

from .utils import create_guid


def add_part_objects_to_ifc(p: Part, f, assembly: Assembly, ifc_include_fem=False):
    # TODO: Consider having all of these operations happen upon import of elements as opposed to one big operation
    #  on export

    part_ifc = p.get_ifc_elem()
    owner_history = assembly.user.to_ifc()
    physical_objects = []
    for m in p.materials.name_map.values():
        f.add(m.ifc_mat)

    for bm in p.beams:
        bm_ifc = bm.get_ifc_elem()
        f.add(bm_ifc)
        physical_objects.append(bm_ifc)

    for pl in p.plates:
        pl_ifc = pl.get_ifc_elem()
        f.add(pl_ifc)
        physical_objects.append(pl_ifc)

    for pi in p.pipes:
        logging.debug(f'Creating IFC Elem for PIPE "{pi.name}"')
        f.add(pi.get_ifc_elem())

    for wall in p.walls:
        f.add(wall.get_ifc_elem())
        physical_objects.append(wall.get_ifc_elem())

    for shp in p.shapes:
        if "ifc_file" in shp.metadata.keys():
            ifc_file = shp.metadata["ifc_file"]
            ifc_f = assembly.get_ifc_source_by_name(ifc_file)
            ifc_elem = ifc_f.by_guid(shp.guid)
            f.add(ifc_elem)
            physical_objects.append(ifc_elem)
        else:
            f.add(shp.get_ifc_elem())
            physical_objects.append(shp.get_ifc_elem())

    if len(p.fem.nodes) > 0 and ifc_include_fem is True:
        to_ifc_fem(p.fem, f)

    if len(physical_objects) == 0:
        return

    f.create_entity(
        "IfcRelContainedInSpatialStructure",
        create_guid(),
        owner_history,
        "Physical model",
        None,
        physical_objects,
        part_ifc,
    )
