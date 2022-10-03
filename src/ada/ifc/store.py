from __future__ import annotations

import os
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.geom

from ada import Assembly, Beam, Pipe, Plate, Shape
from ada.ifc.utils import default_settings

from .read.read_ifc import read_ifc_file
from .write.write_beams import write_ifc_beam
from .write.write_pipe import write_ifc_pipe
from .write.write_plates import write_ifc_plate
from .write.write_shapes import write_ifc_shape


@dataclass
class IfcStore:
    ifc_file_path: os.PathLike
    assembly: Assembly = None
    settings: ifcopenshell.geom.settings = field(default_factory=default_settings)

    f: ifcopenshell.file = None
    data_only: bool = False

    def __post_init__(self):
        self.f = ifcopenshell.open(self.ifc_file_path)

    def to_assembly(self) -> Assembly:
        a = read_ifc_file(self.f, self.settings)
        print(f'Import of IFC file "{self.ifc_file_path}" is complete')
        return a

    def sync(self, a: Assembly = None):
        a = a if self.assembly is None else self.assembly

        for obj in filter(lambda x: x.change_type == x.change_type.ADDED, a.get_all_physical_objects()):
            print(obj)

    def add(self, obj: Beam | Plate | Pipe | Shape):
        if isinstance(obj, Beam):
            write_ifc_beam(obj)
        elif isinstance(obj, Plate):
            write_ifc_plate(obj)
        elif isinstance(obj, Pipe):
            write_ifc_pipe(obj)
        elif issubclass(type(obj), Shape):
            write_ifc_shape(obj)
        else:
            raise NotImplementedError()

    def save_to_file(self, filepath: str | os.PathLike):
        with open(filepath, "w") as f:
            self.f.wrapped_data.write(f)
