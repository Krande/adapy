import logging
import pathlib

from ada import Assembly
from ada.ifc.utils import scale_ifc_file

from ..concepts import IfcRef
from .read_beams import import_ifc_beam
from .read_materials import read_ifc_materials
from .read_parts import read_hierarchy
from .read_plates import import_ifc_plate
from .read_shapes import import_ifc_shape
from .reader_utils import add_to_assembly, get_parent, open_ifc


def read_ifc_file(ifc_file, ifc_settings, elements2part=False, data_only=False) -> Assembly:
    ifc_file = pathlib.Path(ifc_file).resolve().absolute()
    if ifc_file.exists() is False:
        raise FileNotFoundError(f'Unable to find "{ifc_file}"')
    ifc_ref = IfcRef(ifc_file)
    a = Assembly("TempAssembly")
    a.ifc_settings = ifc_settings
    f = open_ifc(ifc_file)

    scaled_ifc = scale_ifc_file(a.ifc_file, f)
    if scaled_ifc is not None:
        f = scaled_ifc

    # Get hierarchy
    if elements2part is None:
        read_hierarchy(f, a, ifc_ref)

    # Get Materials
    read_ifc_materials(f, a, ifc_ref)

    # Get physical elements
    for product in f.by_type("IfcProduct"):
        if product.Representation is None or data_only is True:
            logging.info(f'Passing product "{product}"')
            continue
        parent = get_parent(product)
        obj = import_physical_ifc_elem(product, a, ifc_ref)
        if obj is None:
            continue
        obj.metadata["ifc_file"] = ifc_file
        add_to_assembly(a, obj, parent, elements2part)

    print(f'Import of IFC file "{ifc_file}" is complete')
    return a


def import_physical_ifc_elem(product, assembly: Assembly, ifc_ref: IfcRef):
    pr_type = product.is_a()
    if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
        obj = import_ifc_beam(product, ifc_ref, assembly)
    elif pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
        obj = import_ifc_plate(product, ifc_ref, assembly)
    else:
        if product.is_a("IfcOpeningElement") is True:
            return None
        obj = import_ifc_shape(product, ifc_ref, assembly)

    return obj
