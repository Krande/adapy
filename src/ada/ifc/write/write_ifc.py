from __future__ import annotations

import logging
import os
import pathlib
from io import StringIO
from itertools import chain
from typing import TYPE_CHECKING, Union

from ada import Assembly, Part
from ada.fem.formats.ifc.writer import to_ifc_fem

from ..utils import create_guid
from .write_beams import write_ifc_beam
from .write_instances import write_mapped_instance
from .write_plates import write_ifc_plate
from .write_shapes import write_ifc_shape
from .write_wall import write_ifc_wall

if TYPE_CHECKING:
    import ifcopenshell


def write_to_ifc(
    destination_file,
    a: Assembly,
    include_fem,
    return_file_obj=False,
    create_new_ifc_file=False,
) -> Union[None, StringIO]:
    from ada.ifc.utils import assembly_to_ifc_file

    if create_new_ifc_file:
        f = assembly_to_ifc_file(a)
    else:
        f = a.ifc_file

    for s in a.sections:
        f.add(s.ifc_profile)
        f.add(s.ifc_beam_type)

    for m in a.materials.name_map.values():
        f.add(m.ifc_mat)

    for p in a.get_all_parts_in_assembly(include_self=True):
        add_part_objects_to_ifc(p, f, a, include_fem)

    all_groups = [p.groups.values() for p in a.get_all_parts_in_assembly(include_self=True)]
    for group in chain.from_iterable(all_groups):
        group.to_ifc(f)

    if len(a.presentation_layers) > 0:
        presentation_style = f.createIfcPresentationStyle("HiddenLayers")
        f.createIfcPresentationLayerWithStyle(
            "HiddenLayers",
            "Hidden Layers (ADA)",
            a.presentation_layers,
            "10",
            False,
            False,
            False,
            [presentation_style],
        )

    if return_file_obj:
        return StringIO(f.wrapped_data.to_string())

    dest = pathlib.Path(destination_file).with_suffix(".ifc")
    os.makedirs(dest.parent, exist_ok=True)
    f.write(str(dest))
    a._source_ifc_files = dict()


def add_part_objects_to_ifc(p: Part, f: ifcopenshell.file, assembly: Assembly, ifc_include_fem=False):
    # TODO: Consider having all of these operations happen upon import of elements as opposed to one big operation
    #  on export

    part_ifc = p.get_ifc_elem()
    owner_history = assembly.user.to_ifc()
    physical_objects = []
    for m in p.materials.name_map.values():
        f.add(m.ifc_mat)

    for bm in p.beams:
        bm_ifc = write_ifc_beam(bm)
        f.add(bm_ifc)
        physical_objects.append(bm_ifc)

    for pl in p.plates:
        pl_ifc = write_ifc_plate(pl)
        f.add(pl_ifc)
        physical_objects.append(pl_ifc)

    for pi in p.pipes:
        logging.debug(f'Creating IFC Elem for PIPE "{pi.name}"')
        f.add(pi.get_ifc_elem())

    for wall in p.walls:
        wall_ifc = write_ifc_wall(wall)
        f.add(wall_ifc)
        physical_objects.append(wall_ifc)

    for shp in p.shapes:
        if "ifc_file" in shp.metadata.keys():
            ifc_file = shp.metadata["ifc_file"]
            ifc_f = assembly.get_ifc_source_by_name(ifc_file)
            ifc_elem = ifc_f.by_guid(shp.guid)
            new_ifc_elem = f.add(ifc_elem)

            # Simple check to ensure that the new IFC element is properly copied
            # res = get_container(new_ifc_elem)
            # if res is not None:
            #     parent_ifc_elem_guid = str(res.GlobalId, encoding="utf-8")
            #     parent_guid = str(shp.parent.guid, encoding="utf-8")
            #     if parent_ifc_elem_guid != parent_guid:
            #         logging.warning(f"Parent guid and generated ifc guid differs for element {shp.name}")

            physical_objects.append(new_ifc_elem)
        else:
            ifc_shape = write_ifc_shape(shp)
            f.add(ifc_shape)
            physical_objects.append(ifc_shape)

    for instance in p.instances.values():
        write_mapped_instance(instance, f)

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
