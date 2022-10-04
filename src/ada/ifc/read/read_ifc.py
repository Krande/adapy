from __future__ import annotations

import os

import ifcopenshell

from ada import Assembly
from ada.ifc.utils import get_unit_type


def read_ifc_file(ifc_file: os.PathLike | ifcopenshell.file, elements2part=False, data_only=False) -> Assembly:
    from ada.ifc.store import IfcStore

    ifc_store = IfcStore.from_ifc(ifc_file)

    unit = get_unit_type(ifc_store.f)

    a = Assembly("TempAssembly", units=unit)

    ifc_store.assembly = a

    # Get hierarchy
    if elements2part is None:
        ifc_store.load_spatial_hierarchy()

    # Get Materials
    ifc_store.load_materials()

    # Get physical elements
    ifc_store.load_objects(data_only=data_only)

    ifc_file_name = "object" if isinstance(ifc_file, ifcopenshell.file) else ifc_file

    print(f'Import of IFC file "{ifc_file_name}" is complete')
    return a
