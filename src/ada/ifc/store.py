from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import ifcopenshell
import ifcopenshell.geom

from ada.base.changes import ChangeAction
from ada.ifc.read.read_physical_objects import import_physical_ifc_elem
from ada.ifc.read.reader_utils import (
    add_to_assembly,
    get_ifc_property_sets,
    get_parent,
    resolve_name,
)
from ada.ifc.utils import (
    assembly_to_ifc_file,
    create_guid,
    default_settings,
    get_unit_type,
    write_elem_property_sets,
)
from ada.ifc.write.write_beams import write_ifc_beam
from ada.ifc.write.write_instances import write_mapped_instance
from ada.ifc.write.write_material import write_ifc_mat
from ada.ifc.write.write_pipe import write_ifc_pipe
from ada.ifc.write.write_plates import write_ifc_plate
from ada.ifc.write.write_sections import (
    export_beam_section_profile_def,
    export_ifc_beam_type,
    get_profile_class,
)
from ada.ifc.write.write_shapes import write_ifc_shape
from ada.ifc.write.write_spatial_elements import (
    write_ifc_part,
    write_ifc_spatial_hierarchy,
)
from ada.ifc.write.write_user import create_owner_history_from_user
from ada.ifc.write.write_wall import write_ifc_wall

if TYPE_CHECKING:
    from ada import (
        Assembly,
        Beam,
        Material,
        Part,
        Pipe,
        Plate,
        Section,
        Shape,
        User,
        Wall,
    )


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

    def sync(self, include_fem=False):
        def is_added(x):
            return x.change_type == x.change_type.ADDED

        a = self.assembly
        a.consolidate_sections()

        self.update_owner(a.user)

        if len(list(self.f.by_type("IfcSite"))) == 0:
            write_ifc_spatial_hierarchy(self)

        num_new_spatial_objects = 0
        for part in filter(is_added, a.get_all_parts_in_assembly()):
            self.add_part(part, include_fem=include_fem)
            part.change_type = ChangeAction.NOCHANGE
            num_new_spatial_objects += 1

        self.sync_sections()
        self.sync_materials()

        num_new_objects = 0
        contained_in_spatial = {x.guid: [] for x in a.get_all_parts_in_assembly(include_self=True)}
        for to_be_added in filter(is_added, a.get_all_physical_objects()):
            ifc_elem = self.add(to_be_added)
            write_elem_property_sets(to_be_added.metadata, ifc_elem, self.f, self.owner_history)
            contained_in_spatial[to_be_added.parent.guid].append(ifc_elem)
            to_be_added.change_type = ChangeAction.NOCHANGE
            num_new_objects += 1

        for spatial_elem_guid, relating_elements in contained_in_spatial.items():
            if len(relating_elements) == 0:
                continue
            self.add_related_elements_to_spatial_container(relating_elements, spatial_elem_guid)

        self.sync_mapped_instances()

        print(f"Synced {num_new_objects} objects and {num_new_spatial_objects} spatial elements")

    def sync_sections(self):
        for sec in self.assembly.get_all_sections():
            if sec.change_type != ChangeAction.ADDED:
                continue

            self.create_ifc_profile_def(sec)
            sec.change_type = ChangeAction.NOCHANGE

    def sync_materials(self):
        for mat in self.assembly.get_all_materials():
            if mat.change_type != ChangeAction.ADDED:
                continue
            self.create_ifc_material(mat)
            mat.change_type = ChangeAction.NOCHANGE

    def sync_mapped_instances(self):
        for part in self.assembly.get_all_parts_in_assembly(include_self=True):
            for instance in part.instances.values():
                write_mapped_instance(instance, self.f)

    def add_part(self, part: Part, include_fem):
        return write_ifc_part(self, part, include_fem=include_fem)

    def add(self, obj: Beam | Plate | Pipe | Shape | Wall) -> ifcopenshell.entity_instance:
        from ada import Beam, Pipe, Plate, Shape, Wall

        if isinstance(obj, Beam):
            return write_ifc_beam(self, obj)
        elif isinstance(obj, Plate):
            return write_ifc_plate(obj)
        elif isinstance(obj, Pipe):
            return write_ifc_pipe(obj)
        elif issubclass(type(obj), Shape):
            return write_ifc_shape(obj)
        elif isinstance(obj, Wall):
            return write_ifc_wall(obj)
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

        target_units = None
        unit_type = get_unit_type(self.f)

        if unit_type != self.assembly.units:
            target_units = self.assembly.units
            self.assembly.units = unit_type

        if elements2part is None:
            self.load_spatial_hierarchy()

        # Load Materials
        self.load_materials()

        # Load physical elements
        self.load_objects(data_only=data_only, elements2part=elements2part)

        if target_units is not None:
            self.assembly.units = target_units

        ifc_file_name = "object" if self.ifc_file_path is None else self.ifc_file_path

        for obj in self.assembly.get_all_sections():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_materials():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_physical_objects():
            obj.change_type = ChangeAction.NOCHANGE

        for obj in self.assembly.get_all_parts_in_assembly(include_self=True):
            obj.change_type = ChangeAction.NOCHANGE

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

    def get_ifc_geom_iterator(self, settings: ifcopenshell.geom.settings):
        import multiprocessing

        products = [self.f.by_guid(x.guid) for x in self.assembly.get_all_physical_objects()]
        return ifcopenshell.geom.iterator(settings, self.f, multiprocessing.cpu_count(), include=products)

    def get_by_guid(self, guid: str) -> ifcopenshell.entity_instance:
        return self.f.by_guid(guid)

    def load_objects(self, data_only=False, elements2part=None):
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

            obj.metadata = props
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

    def associate_elem_with_material(self, material: Material, ifc_elem: ifcopenshell.entity_instance):
        rel_mat = self.f.by_guid(material.guid)
        related_objects = [*rel_mat.RelatedObjects, ifc_elem]
        rel_mat.RelatedObjects = related_objects
        return rel_mat

    def associate_elem_with_profiledef(self, section: Section, ifc_elem: ifcopenshell.entity_instance):
        rel_profile_def = self.f.by_guid(section.guid)
        related_objects = [*rel_profile_def.RelatedObjects, ifc_elem]
        rel_profile_def.RelatedObjects = related_objects
        return rel_profile_def

    def create_ifc_profile_def(self, section: Section):
        export_beam_section_profile_def(section)
        _ = export_ifc_beam_type(section)

    def get_profile_def(self, section: Section) -> ifcopenshell.entity_instance:
        profile_class = get_profile_class(section)
        for profile_def in self.f.by_type(profile_class.get_ifc_type()):
            if profile_def.ProfileName == section.name:
                return profile_def

    def create_ifc_material(self, material: Material):
        ifc_mat = write_ifc_mat(material)

        return self.f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=material.guid,
            OwnerHistory=self.owner_history,
            Name=ifc_mat.Name,
            Description=f"Objects related to {ifc_mat.Name}",
            RelatedObjects=[],
            RelatingMaterial=ifc_mat,
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
