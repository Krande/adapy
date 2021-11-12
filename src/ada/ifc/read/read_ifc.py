import logging

from ada import Assembly
from ada.ifc.utils import scale_ifc_file

from .read_beams import import_ifc_beam
from .read_materials import read_ifc_materials
from .read_parts import read_hierarchy
from .read_plates import import_ifc_plate
from .read_shapes import import_general_shape
from .reader_utils import (
    add_to_assembly,
    get_name,
    get_parent,
    getIfcPropertySets,
    open_ifc,
)


def read_ifc_file(ifc_file, ifc_settings, elements2part=False, data_only=False) -> Assembly:

    a = Assembly("TempAssembly")

    f = open_ifc(ifc_file)

    scaled_ifc = scale_ifc_file(a.ifc_file, f)
    if scaled_ifc is not None:
        f = scaled_ifc

    # Get hierarchy
    if elements2part is None:
        read_hierarchy(f, a)

    # Get Materials
    read_ifc_materials(f, a)

    # Get physical elements
    for product in f.by_type("IfcProduct"):
        if product.Representation is None or data_only is True:
            logging.info(f'Passing product "{product}"')
            continue
        parent = get_parent(product)
        obj = import_physical_ifc_elem(product, ifc_settings)
        obj.metadata["ifc_file"] = ifc_file
        if obj is not None:
            add_to_assembly(a, obj, parent, elements2part)

    print(f'Import of IFC file "{ifc_file}" is complete')
    return a


def import_physical_ifc_elem(product, ifc_settings):
    pr_type = product.is_a()

    props = getIfcPropertySets(product)
    name = get_name(product)
    logging.info(f"importing {name}")
    if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
        obj = import_ifc_beam(product, name, props, ifc_settings)
    elif pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
        obj = import_ifc_plate(product, name, props, ifc_settings)
    else:
        if product.is_a("IfcOpeningElement") is True:
            return None
        obj = import_general_shape(product, name, props, ifc_settings)

    return obj
