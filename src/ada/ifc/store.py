from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.geom

from ada import Assembly, Beam, Pipe, Plate, Shape
from ada.ifc.read.read_physical_objects import import_physical_ifc_elem
from ada.ifc.read.reader_utils import (
    add_to_assembly,
    get_ifc_property_sets,
    get_parent,
    resolve_name,
)
from ada.ifc.utils import default_settings

from .write.write_beams import write_ifc_beam
from .write.write_pipe import write_ifc_pipe
from .write.write_plates import write_ifc_plate
from .write.write_shapes import write_ifc_shape


@dataclass
class IfcStore:
    ifc_file_path: os.PathLike = None
    assembly: Assembly = None
    settings: ifcopenshell.geom.settings = field(default_factory=default_settings)

    f: ifcopenshell.file = None
    data_only: bool = False
    elements2part: bool = False

    def __post_init__(self):
        if self.f is None:
            self.f = ifcopenshell.open(self.ifc_file_path)

    def to_assembly(self) -> Assembly:
        from .read.read_ifc import read_ifc_file

        a = read_ifc_file(self.f, self.settings)
        print(f'Import of IFC file "{self.ifc_file_path}" is complete')
        return a

    def sync(self, a: Assembly = None):
        a = a if self.assembly is None else self.assembly

        for added_ifc in filter(lambda x: x.change_type == x.change_type.ADDED, a.get_all_physical_objects()):
            print(f"{added_ifc=}")

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

    @staticmethod
    def from_ifc_file_path(ifc_file: str | os.PathLike) -> IfcStore:
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        if ifc_file.exists() is False:
            raise FileNotFoundError(f'Unable to find "{ifc_file}"')

        f = ifcopenshell.open(str(ifc_file))
        return IfcStore(ifc_file, f=f)

    @staticmethod
    def from_ifc_obj(ifc_file: ifcopenshell.file) -> IfcStore:
        # Make a copy of the model
        f = ifcopenshell.file.from_string(ifc_file.wrapped_data.to_string())
        return IfcStore(f=f)

    def load_hierarchies(self):
        from .read.read_parts import PartImporter

        pi = PartImporter(self)
        pi.load_hierarchies()

    def load_materials(self):
        from .read.read_materials import MaterialImporter

        mi = MaterialImporter(self)
        mi.load_ifc_materials()

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    def load_objects(self):
        for product in self.f.by_type("IfcProduct"):
            if product.Representation is None or self.data_only is True:
                logging.info(f'Passing product "{product}"')
                continue
            parent = get_parent(product)
            name = product.Name

            if parent is None:
                logging.debug(f'Skipping "{name}". Parent is None')
                continue

            props = get_ifc_property_sets(product)

            if name is None:
                name = resolve_name(props, product)

            logging.info(f"importing {name}")

            obj = import_physical_ifc_elem(product, name, self)
            if obj is None:
                continue

            obj.metadata.update(dict(props=props))
            obj.metadata["ifc_file"] = self.f
            obj.metadata["ifc_guid"] = product.GlobalId

            add_to_assembly(self.assembly, obj, parent, self.elements2part)
