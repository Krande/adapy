from __future__ import annotations

import logging
import os
import pathlib

import ifcopenshell
from ifcopenshell.util.element import get_psets

from ada import Assembly
from ada.ifc.utils import get_unit_type

from ..concepts import IfcRef
from .read_beams import import_ifc_beam
from .read_materials import read_ifc_materials
from .read_parts import read_hierarchy
from .read_pipe import import_pipe_segment
from .read_plates import import_ifc_plate
from .read_shapes import import_ifc_shape
from .reader_utils import add_to_assembly, get_parent, open_ifc, resolve_name


def read_ifc_file(
    ifc_file: os.PathLike | ifcopenshell.file, ifc_settings, elements2part=False, data_only=False
) -> Assembly:
    if isinstance(ifc_file, ifcopenshell.file):
        # Make a copy of the model
        f = ifcopenshell.file.from_string(ifc_file.wrapped_data.to_string())
    elif isinstance(ifc_file, (os.PathLike, str)):
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        if ifc_file.exists() is False:
            raise FileNotFoundError(f'Unable to find "{ifc_file}"')

        f = open_ifc(ifc_file)
    else:
        raise ValueError(f'Unrecognized type "{type(ifc_file)}"')

    ifc_ref = IfcRef(ifc_file)
    unit = get_unit_type(f)
    a = Assembly("TempAssembly", units=unit)
    a.ifc_settings = ifc_settings

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
        name = product.Name

        if parent is None:
            logging.debug(f'Skipping "{name}". Parent is None')
            continue

        props = get_psets(product)

        if name is None:
            name = resolve_name(props, product)

        logging.info(f"importing {name}")

        obj = import_physical_ifc_elem(product, name, a, ifc_ref)
        if obj is None:
            continue

        obj.metadata.update(dict(props=props))
        obj.metadata["ifc_file"] = ifc_file
        obj.metadata["ifc_guid"] = product.GlobalId

        add_to_assembly(a, obj, parent, elements2part)
    ifc_file_name = "object" if isinstance(ifc_file, ifcopenshell.file) else ifc_file

    print(f'Import of IFC file "{ifc_file_name}" is complete')
    return a


def import_physical_ifc_elem(product, name, assembly: Assembly, ifc_ref: IfcRef):
    from .exceptions import NoIfcAxesAttachedError

    pr_type = product.is_a()

    if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
        try:
            return import_ifc_beam(product, name, ifc_ref, assembly)
        except NoIfcAxesAttachedError as e:
            logging.debug(e)
            pass
    if pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
        try:
            return import_ifc_plate(product, name, ifc_ref, assembly)
        except NoIfcAxesAttachedError as e:
            logging.debug(e)
            pass

    if product.is_a("IfcOpeningElement") is True:
        logging.info(f'skipping opening element "{product}"')
        return None

    if product.is_a() in ("IfcPipeSegment", "IfcPipeFitting"):
        return import_pipe_segment(product, name, ifc_ref, assembly)

    obj = import_ifc_shape(product, name, ifc_ref, assembly)

    return obj
