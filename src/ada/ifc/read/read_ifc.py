from __future__ import annotations

import os

import ifcopenshell

from ada import Assembly
from ada.ifc.utils import get_unit_type


def read_ifc_file(
    ifc_file: os.PathLike | ifcopenshell.file, ifc_settings, elements2part=False, data_only=False
) -> Assembly:
    from ada.ifc.store import IfcStore

    if isinstance(ifc_file, ifcopenshell.file):
        ifc_store = IfcStore.from_ifc_obj(ifc_file)
    elif isinstance(ifc_file, (os.PathLike, str)):
        ifc_store = IfcStore.from_ifc_file_path(ifc_file)
    else:
        raise ValueError(f'Unrecognized type "{type(ifc_file)}"')

    unit = get_unit_type(ifc_store.f)

    a = Assembly("TempAssembly", units=unit)
    a.ifc_settings = ifc_settings

    ifc_store.assembly = a
    ifc_store.elements2part = elements2part
    ifc_store.data_only = data_only

    # Get hierarchy
    if elements2part is None:
        ifc_store.load_hierarchies()

    # Get Materials
    ifc_store.load_materials()

    # Get physical elements
    ifc_store.load_objects()

    ifc_file_name = "object" if isinstance(ifc_file, ifcopenshell.file) else ifc_file

    print(f'Import of IFC file "{ifc_file_name}" is complete')
    return a
