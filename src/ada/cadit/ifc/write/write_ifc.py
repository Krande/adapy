from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import ifcopenshell
import ifcopenshell.api.material
import ifcopenshell.geom

import ada
from ada.base.changes import ChangeAction
from ada.cadit.ifc.read.reader_utils import get_ifc_body
from ada.cadit.ifc.utils import add_negative_extrusion, write_elem_property_sets
from ada.cadit.ifc.write.write_beams import update_ifc_beam, write_ifc_beam
from ada.cadit.ifc.write.write_fasteners import write_ifc_fastener
from ada.cadit.ifc.write.write_instances import write_mapped_instance
from ada.cadit.ifc.write.write_material import write_ifc_mat
from ada.cadit.ifc.write.write_openings import generate_ifc_opening
from ada.cadit.ifc.write.write_pipe import write_ifc_pipe
from ada.cadit.ifc.write.write_plates import write_ifc_plate, write_ifc_plate_curved
from ada.cadit.ifc.write.write_sections import export_beam_section_profile_def
from ada.cadit.ifc.write.write_shapes import write_ifc_shape
from ada.cadit.ifc.write.write_spatial_elements import (
    write_ifc_part,
    write_ifc_spatial_hierarchy,
)
from ada.cadit.ifc.write.write_wall import write_ifc_wall
from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada import Beam, Material, Part, Pipe, Plate, Section, Shape, Wall
    from ada.cadit.ifc.store import IfcStore


def is_added(x):
    return x.change_type == ChangeAction.ADDED


def is_modified(x):
    return x.change_type == ChangeAction.MODIFIED


def is_deleted(x):
    return x.change_type == ChangeAction.DELETED


def _default_color_name_gen():
    return ada.Counter(prefix="Color", start=1)


@dataclass
class IfcWriter:
    ifc_store: IfcStore
    callback: Callable[[int, int], None] = None
    color_name_gen: ada.Counter = field(default_factory=_default_color_name_gen)

    def sync_spatial_hierarchy(self, include_fem=False) -> int:
        if len(list(self.ifc_store.f.by_type("IfcSite"))) == 0:
            write_ifc_spatial_hierarchy(self.ifc_store)

        num_new_spatial_objects = 0
        for part in filter(is_added, self.ifc_store.assembly.get_all_parts_in_assembly()):
            self.add_part(part, include_fem=include_fem)
            part.change_type = ChangeAction.NOCHANGE
            num_new_spatial_objects += 1
        return num_new_spatial_objects

    def sync_added_geom_repr(self):
        logger.warning("A Geom representation override dict is passed to ifc sync(). This is not yet implemented")
        ...

    def sync_added_physical_objects(self) -> int:
        from ada import Pipe

        a = self.ifc_store.assembly
        mat_map = {mat.guid: mat for mat in a.get_all_materials()}
        rel_mats_map = {
            m.GlobalId: m
            for m in filter(
                lambda x: x.RelatingMaterial.is_a("IfcMaterial"), self.ifc_store.f.by_type("IfcRelAssociatesMaterial")
            )
        }
        new_objects = list(filter(is_added, list(a.get_all_physical_objects())))
        num_new_objects = len(new_objects)
        contained_in_spatial = {x.guid: [] for x in a.get_all_parts_in_assembly(include_self=True)}

        for i, to_be_added in enumerate(new_objects, start=1):
            self.eval_validity(to_be_added, mat_map, rel_mats_map)

            ifc_elem = self.add(to_be_added)
            self.create_ifc_openings(to_be_added, ifc_elem)

            write_elem_property_sets(to_be_added.metadata, ifc_elem, self.ifc_store.f, self.ifc_store.owner_history)

            contained_in_spatial[to_be_added.parent.guid].append(ifc_elem)
            to_be_added.change_type = ChangeAction.NOCHANGE
            if self.callback is not None:
                self.callback(i, num_new_objects)

        # Create relationships between materials and physical objects here inside the object creation
        obj_map = defaultdict(list)
        for obj in new_objects:
            if not hasattr(obj, "material"):
                continue
            obj_map[obj.material].append(obj)

        for mat, objects in obj_map.items():
            rel_mat = self.ifc_store.f.by_guid(mat.guid)
            ifc_elems = [self.ifc_store.f.by_guid(obj.guid) for obj in objects if not isinstance(obj, Pipe)]
            ifc_elems_pipe_seg = [
                self.ifc_store.f.by_guid(seg.guid) for obj in objects if isinstance(obj, Pipe) for seg in obj.segments
            ]
            rel_mat.RelatedObjects = [*rel_mat.RelatedObjects, *ifc_elems, *ifc_elems_pipe_seg]

        for spatial_elem_guid, relating_elements in contained_in_spatial.items():
            if len(relating_elements) == 0:
                continue
            self.add_related_elements_to_spatial_container(relating_elements, spatial_elem_guid)

        return num_new_objects

    def sync_modified_physical_objects(self) -> int:
        num_mod = 0
        for to_be_modified in filter(is_modified, self.ifc_store.assembly.get_all_physical_objects()):
            self.create_ifc_openings(to_be_modified)
            to_be_modified.change_type = ChangeAction.NOCHANGE
            num_mod += 1
        return num_mod

    def sync_added_welds(self):
        for weld in self.ifc_store.assembly.welds:
            ifc_weld = write_ifc_fastener(weld)
            self.add_related_elements_to_spatial_container([ifc_weld], weld.parent.guid)
            if weld.groove is None:
                continue

            for mem in weld.members:
                rel_ifc_mem = self.ifc_store.f.by_guid(mem.guid)
                ifc_opening = generate_ifc_opening(weld.groove)
                self.ifc_store.f.create_entity(
                    "IfcRelVoidsElement",
                    GlobalId=create_guid(),
                    OwnerHistory=self.ifc_store.owner_history,
                    Name=None,
                    Description=None,
                    RelatingBuildingElement=rel_ifc_mem,
                    RelatedOpeningElement=ifc_opening,
                )

    def sync_deleted_physical_objects(self) -> int:
        num_mod = 0
        for to_be_modified in filter(is_deleted, self.ifc_store.assembly.get_all_physical_objects()):
            self.create_ifc_openings(to_be_modified)
            to_be_modified.change_type = ChangeAction.NOCHANGE
            num_mod += 1
        return num_mod

    def sync_groups(self):
        f = self.ifc_store.f
        for p in self.ifc_store.assembly.get_all_parts_in_assembly(include_self=True):
            for group in p.groups.values():
                if group.change_type == ChangeAction.ADDED:
                    ifc_group = f.create_entity(
                        "IfcGroup",
                        GlobalId=group.guid,
                        OwnerHistory=self.ifc_store.owner_history,
                        Name=group.name,
                        Description=group.description,
                    )
                    f.create_entity(
                        "IfcRelAssignsToGroup",
                        GlobalId=create_guid(),
                        OwnerHistory=self.ifc_store.owner_history,
                        Name=None,
                        Description=None,
                        RelatingGroup=ifc_group,
                        RelatedObjects=[f.by_guid(x.guid) for x in group.members],
                    )
                elif group.change_type == ChangeAction.MODIFIED:
                    ifc_group = f.by_guid(group.guid)
                    ifc_group.Name = group.name
                    ifc_group.Description = group.description
                    ifc_group.RelatedObjects = [f.by_guid(x.guid) for x in group.members]
                elif group.change_type == ChangeAction.NOCHANGE:
                    pass
                else:
                    raise NotImplementedError(f"Group change type {group.change_type} is not supported")

                group.change_type = ChangeAction.NOCHANGE

    def sync_presentation_layers(self) -> int:
        from ada import Boolean, Part, Pipe

        num_added = 0
        f = self.ifc_store.f

        def append_bodies(ifc_ref: str | ifcopenshell.entity_instance, assigned_items_):
            if isinstance(ifc_ref, str):
                ifc_obj_ = f.by_guid(ifc_ref)
            else:
                ifc_obj_ = ifc_ref

            ifc_body_ = get_ifc_body(ifc_obj_, allow_multiple=True)
            if isinstance(ifc_body_, list):
                for body_ in ifc_body_:
                    assigned_items_.append(body_)
            else:
                assigned_items_.append(ifc_body_)

        for layer in self.ifc_store.assembly.presentation_layers.layers.values():
            assigned_items = []
            for member in layer.members:
                if isinstance(member, Pipe):
                    for seg in member.segments:
                        append_bodies(seg.guid, assigned_items)

                elif isinstance(member, Part):
                    continue
                elif isinstance(member, Boolean):
                    for ifc_opening in f.by_type("IfcOpeningElement"):
                        if ifc_opening.Name != member.name:
                            continue
                        append_bodies(ifc_opening, assigned_items)

                else:
                    append_bodies(member.guid, assigned_items)

            exist_layer = None
            for ifc_layer in f.by_type("IfcPresentationLayerAssignment"):
                if ifc_layer.Name == layer.name:
                    exist_layer = ifc_layer
                    break
            if len(assigned_items) == 0:
                continue

            if exist_layer is None:
                f.create_entity(
                    "IfcPresentationLayerAssignment",
                    Name=layer.name,
                    Description=layer.description,
                    AssignedItems=assigned_items,
                    Identifier=layer.identifier,
                    # LayerOn=True,
                    # LayerFrozen=False,
                    # LayerBlocked=False,
                    # LayerStyles=[presentation_style],
                )
            else:
                updated_assigned_items = list(exist_layer.AssignedItems)
                for ai in assigned_items:
                    if ai not in updated_assigned_items:
                        updated_assigned_items.append(ai)
                exist_layer.AssignedItems = updated_assigned_items

            layer.change_type = ChangeAction.NOCHANGE

        return num_added

    def sync_mapped_instances(self):
        for part in self.ifc_store.assembly.get_all_parts_in_assembly(include_self=True):
            for instance in part.instances.values():
                write_mapped_instance(instance, self.ifc_store.f)

    def sync_sections(self):
        for sec in self.ifc_store.assembly.get_all_sections():
            if sec.change_type != ChangeAction.ADDED:
                continue

            self.create_ifc_profile_def(sec)
            self.create_ifc_beam_type(sec)
            sec.change_type = ChangeAction.NOCHANGE

    def sync_materials(self):
        all_mats = self.ifc_store.assembly.get_all_materials()
        skipped_mats = []
        for mat in all_mats:
            if mat.change_type != ChangeAction.ADDED:
                skipped_mats.append(mat)
                continue
            self.create_ifc_material(mat)
            mat.change_type = ChangeAction.NOCHANGE

        skip_mats = set([m.guid for m in skipped_mats])
        mat_map = {mat.guid for mat in self.ifc_store.assembly.get_all_materials()} - skip_mats

        rel_mats_map = {
            m.GlobalId
            for m in filter(
                lambda x: x.RelatingMaterial.is_a("IfcMaterial"), self.ifc_store.f.by_type("IfcRelAssociatesMaterial")
            )
        }
        if len(mat_map - rel_mats_map) != 0:
            raise ValueError("Syncing of Materials failed")

    def create_ifc_openings(self, obj: Beam | Plate | Pipe | Shape | Wall, ifc_obj=None):
        from ada import Part, Wall
        from ada.core.constants import O, X, Z

        f = self.ifc_store.f

        if ifc_obj is None:
            ifc_obj = f.by_guid(obj.guid)

        if isinstance(obj, Wall):
            if len(obj.inserts) > 0:
                for i, insert in enumerate(obj.inserts):
                    add_negative_extrusion(f, O, Z, X, insert.height, obj.openings_extrusions[i], ifc_obj)
                    if issubclass(type(insert), Part) is False:
                        raise ValueError(f'Unrecognized type "{type(insert)}"')

        else:
            if len(obj.booleans) > 0:
                for pen in obj.booleans:
                    ifc_opening = generate_ifc_opening(pen)
                    f.create_entity(
                        "IfcRelVoidsElement",
                        GlobalId=create_guid(),
                        OwnerHistory=self.ifc_store.owner_history,
                        Name=None,
                        Description=None,
                        RelatingBuildingElement=ifc_obj,
                        RelatedOpeningElement=ifc_opening,
                    )

    def eval_validity(self, to_be_added, mat_map, rel_mats_map):
        from ada import Pipe, Shape, Wall

        if isinstance(to_be_added, Wall) is False and issubclass(type(to_be_added), Shape) is False:
            if to_be_added.material.guid not in mat_map.keys():
                raise ValueError(f"Object {to_be_added.material} is not among synced materials {mat_map}")
            if to_be_added.material.guid not in rel_mats_map.keys():
                raise ValueError(f"Object {to_be_added.material} is not among synced materials {rel_mats_map}")

        elif isinstance(to_be_added, Pipe):
            for seg in to_be_added.segments:
                if seg.material.guid not in mat_map.keys():
                    raise ValueError(f"Object {to_be_added.material} is not among synced materials {mat_map}")
                if seg.material.guid not in rel_mats_map.keys():
                    raise ValueError(f"Object {to_be_added.material} is not among synced materials {rel_mats_map}")

    def add_related_elements_to_spatial_container(self, elements: list[ifcopenshell.entity_instance], guid: str):
        parent_ifc_elem = self.ifc_store.get_by_guid(guid)

        existing_spatial = None
        for existing_rel in self.ifc_store.f.by_type("IfcRelContainedInSpatialStructure"):
            if parent_ifc_elem == existing_rel.RelatingStructure:
                existing_spatial = existing_rel
                break

        if existing_spatial is not None:
            existing_spatial.OwnerHistory = self.ifc_store.owner_history
            existing_spatial.RelatedElements = list(existing_spatial.RelatedElements) + elements
        else:
            self.ifc_store.f.create_entity(
                "IfcRelContainedInSpatialStructure",
                GlobalId=create_guid(),
                OwnerHistory=self.ifc_store.owner_history,
                Name="Physical model",
                Description=None,
                RelatedElements=elements,
                RelatingStructure=parent_ifc_elem,
            )

    def associate_elem_with_material(self, material: Material, ifc_elem: ifcopenshell.entity_instance):
        rel_mat = self.ifc_store.f.by_guid(material.guid)
        related_objects = [*rel_mat.RelatedObjects, ifc_elem]
        rel_mat.RelatedObjects = related_objects
        return rel_mat

    def associate_elem_with_profiledef(self, section: Section, ifc_elem: ifcopenshell.entity_instance):
        """This is only for IFC 4.3++"""
        rel_profile_def = self.ifc_store.f.by_guid(section.guid)
        related_objects = [*rel_profile_def.RelatedObjects, ifc_elem]
        rel_profile_def.RelatedObjects = related_objects
        return rel_profile_def

    def add(self, obj: Beam | Plate | Pipe | Shape | Wall) -> ifcopenshell.entity_instance:
        from ada import Beam, Pipe, Plate, PlateCurved, Shape, Wall

        if isinstance(obj, Beam):
            return write_ifc_beam(self.ifc_store, obj)
        elif isinstance(obj, Plate):
            return write_ifc_plate(obj)
        elif isinstance(obj, PlateCurved):
            return write_ifc_plate_curved(obj)
        elif isinstance(obj, Pipe):
            return write_ifc_pipe(obj)
        elif issubclass(type(obj), Shape):
            return write_ifc_shape(self.ifc_store, obj)
        elif isinstance(obj, Wall):
            return write_ifc_wall(obj)
        else:
            raise NotImplementedError(f"Object {obj} is not supported")

    def update(self, obj: Beam | Plate | Pipe | Shape | Wall) -> ifcopenshell.entity_instance:
        from ada import Beam, Pipe, Plate, Shape, Wall

        if isinstance(obj, Beam):
            return update_ifc_beam(self.ifc_store, obj)
        elif isinstance(obj, Plate):
            return write_ifc_plate(obj)
        elif isinstance(obj, Pipe):
            return write_ifc_pipe(obj)
        elif issubclass(type(obj), Shape):
            return write_ifc_shape(obj)
        elif isinstance(obj, Wall):
            return write_ifc_wall(obj)
        else:
            raise NotImplementedError(f"Object {obj} is not supported")

    def add_part(self, part: Part, include_fem):
        return write_ifc_part(self.ifc_store, part, include_fem=include_fem)

    def create_ifc_profile_def(self, section: Section):
        export_beam_section_profile_def(section)

    def create_ifc_beam_type(self, section: Section):
        self.ifc_store.f.create_entity(
            "IfcBeamType",
            GlobalId=section.guid,
            OwnerHistory=self.ifc_store.owner_history,
            Name=section.name,
            Description=section.sec_str,
            PredefinedType="BEAM",
        )

    def create_ifc_material(self, material: Material):
        ifc_mat = write_ifc_mat(material)
        self.create_rel_associates_material(material.guid, ifc_mat)
        return ifc_mat

    def create_rel_associates_material(self, guid: str, relating_mat: ifcopenshell.entity_instance, related_objs=None):
        return self.ifc_store.f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=guid,
            OwnerHistory=self.ifc_store.owner_history,
            Name=relating_mat.Name if hasattr(relating_mat, "Name") else None,
            Description=f"Objects related to {relating_mat.Name}" if hasattr(relating_mat, "Description") else None,
            RelatedObjects=() if related_objs is None else related_objs,
            RelatingMaterial=relating_mat,
        )
