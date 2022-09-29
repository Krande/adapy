import ifcopenshell
import os
from dataclasses import dataclass
from ada import Assembly, Part
from .read.read_parts import import_ifc_hierarchy, read_hierarchy
from .concepts import IfcRef


@dataclass
class IfcStore:
    ifc_file_path: os.PathLike

    def to_assembly(self) -> Assembly:
        f = self.load_ifc()
        ifc_ref = IfcRef(self.ifc_file_path)
        a = Assembly()
        a += read_hierarchy(f, a, ifc_ref)

        return a

    def load_ifc(self) -> ifcopenshell.file:
        return ifcopenshell.open(self.ifc_file_path)