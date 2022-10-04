from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import ifcopenshell
import ifcopenshell.geom

from ada.ifc.read.read_physical_objects import import_physical_ifc_elem
from ada.ifc.read.reader_utils import (
    add_to_assembly,
    get_ifc_property_sets,
    get_parent,
    resolve_name,
)
from ada.ifc.utils import assembly_to_ifc_file, create_guid, default_settings

from .write.write_beams import write_ifc_beam
from .write.write_pipe import write_ifc_pipe
from .write.write_plates import write_ifc_plate
from .write.write_shapes import write_ifc_shape
from .write.write_spatial_elements import write_ifc_part, write_ifc_spatial_hierarchy
from .write.write_user import create_owner_history_from_user

if TYPE_CHECKING:
    from ada import Assembly, Beam, Pipe, Plate, Shape, User


@dataclass
class IfcStore:
    ifc_file_path: pathlib.Path | os.PathLike = None
    assembly: Assembly = None
    settings: ifcopenshell.geom.settings = field(default_factory=default_settings)

    f: ifcopenshell.file = None
    owner_history: ifcopenshell.entity_instance = None

    def __post_init__(self):
        if self.f is None:
            if self.ifc_file_path is not None:
                self.ifc_file_path = pathlib.Path(self.ifc_file_path)
                if self.ifc_file_path.exists():
                    self.f = ifcopenshell.open(self.ifc_file_path)
            elif self.assembly is not None:
                self.f = assembly_to_ifc_file(self.assembly)

    def update_owner(self, user: User):
        self.owner_history = create_owner_history_from_user(user, self.f)

    def sync(self):
        def is_added(x):
            return x.change_type == x.change_type.ADDED

        a = self.assembly
        self.update_owner(a.user)

        if len(list(self.f.by_type("IfcSite"))) == 0:
            write_ifc_spatial_hierarchy(self)

        num_new_spatial_objects = 0
        for part in filter(is_added, a.get_all_parts_in_assembly()):
            self.add(part)
            num_new_spatial_objects += 1

        num_new_objects = 0

        contained_in_spatial = {x.guid: [] for x in a.get_all_parts_in_assembly(include_self=True)}
        for to_be_added in filter(is_added, a.get_all_physical_objects()):
            ifc_elem = self.add(to_be_added)
            parent_guid = to_be_added.parent.guid
            if parent_guid not in contained_in_spatial.keys():
                print("sd")
            contained_in_spatial[to_be_added.parent.guid].append(ifc_elem)
            num_new_objects += 1

        for spatial_elem_guid, relating_elements in contained_in_spatial.items():
            self.add_related_elements_to_spatial_container(relating_elements, spatial_elem_guid)

        print(f"Synced {num_new_objects} objects and {num_new_spatial_objects} spatial elements")

    def add(self, obj: Beam | Plate | Pipe | Shape) -> ifcopenshell.entity_instance:
        from ada import Beam, Part, Pipe, Plate, Shape

        if isinstance(obj, Beam):
            return write_ifc_beam(self, obj)
        elif isinstance(obj, Plate):
            return write_ifc_plate(obj)
        elif isinstance(obj, Pipe):
            return write_ifc_pipe(obj)
        elif issubclass(type(obj), Shape):
            return write_ifc_shape(obj)
        elif issubclass(type(obj), Part):
            return write_ifc_part(self, obj)
        else:
            raise NotImplementedError()

    def save_to_file(self, filepath: str | os.PathLike):
        with open(filepath, "w") as f:
            f.write(self.f.wrapped_data.to_string())

    def load_ifc_content_from_file(
        self, ifc_file: str | os.PathLike | ifcopenshell.file = None, data_only=False, elements2part=None
    ) -> None:
        if self.ifc_file_path is None:
            if ifc_file is None:
                raise ValueError("No ifc file is attached")
            if isinstance(ifc_file, (str, os.PathLike)):
                self.ifc_file_path = ifc_file
                self.f = IfcStore.ifc_obj_from_ifc_file(ifc_file)
            else:
                self.f = ifc_file

        if self.assembly is None:
            raise ValueError("Assembly must be attached before loading IFC content")

        if elements2part is None:
            self.load_spatial_hierarchy()

        # Load Materials
        self.load_materials()

        # Load physical elements
        self.load_objects(data_only=data_only)

        ifc_file_name = "object" if self.ifc_file_path is None else self.ifc_file_path

        print(f'Import of IFC file "{ifc_file_name}" is complete')

    def load_spatial_hierarchy(self):
        from .read.read_parts import PartImporter

        pi = PartImporter(self)
        pi.load_hierarchies()

    def load_materials(self):
        from .read.read_materials import MaterialImporter

        mi = MaterialImporter(self)
        mi.load_ifc_materials()

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    def get_by_guid(self, guid: str) -> ifcopenshell.entity_instance:
        return self.f.by_guid(guid)

    def load_objects(self, data_only=False, elements2part=False):
        for product in self.f.by_type("IfcProduct"):
            if product.Representation is None or data_only is True:
                logging.info(f'Passing product "{product}"')
                continue

            parent = get_parent(product)
            name = product.Name
            props = get_ifc_property_sets(product)

            if name is None:
                name = resolve_name(props, product)

            logging.info(f"importing {name}")

            obj = import_physical_ifc_elem(product, name, self)
            if obj is None:
                continue

            add_to_assembly(self.assembly, obj, parent, elements2part)

    def add_related_elements_to_spatial_container(self, elements: list[ifcopenshell.entity_instance], guid: str):
        parent_ifc_elem = self.get_by_guid(guid)
        self.f.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=create_guid(),
            OwnerHistory=self.owner_history,
            Name="Physical model",
            Description=None,
            RelatedElements=elements,
            RelatingStructure=parent_ifc_elem,
        )

    @staticmethod
    def from_ifc(ifc_file: str | os.PathLike | ifcopenshell.file, make_a_copy=True) -> IfcStore:
        ifc_file_path = None

        if isinstance(ifc_file, (str, os.PathLike)):
            ifc_file_path = ifc_file
            f = IfcStore.ifc_obj_from_ifc_file(ifc_file)
        else:
            if make_a_copy:
                f = IfcStore.copy_ifc_obj(ifc_file)
            else:
                f = ifc_file

        return IfcStore(ifc_file_path=ifc_file_path, f=f)

    @staticmethod
    def ifc_obj_from_ifc_file(ifc_file: str | os.PathLike) -> ifcopenshell.file:
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        if ifc_file.exists() is False:
            raise FileNotFoundError(f'Unable to find "{ifc_file}"')
        return ifcopenshell.open(str(ifc_file))

    @staticmethod
    def copy_ifc_obj(ifc_file: ifcopenshell.file) -> ifcopenshell.file:
        return ifcopenshell.file.from_string(ifc_file.wrapped_data.to_string())
