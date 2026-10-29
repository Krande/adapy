from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import create_local_placement, write_elem_property_sets
from ada.core.guid import create_guid
from ada.fem.formats.ifc.writer import to_ifc_fem

if TYPE_CHECKING:
    from ada import Part
    from ada.cadit.ifc.store import IfcStore


IMPLICIT_BUILDING_TAG = "ada:implicit-building"


def write_ifc_spatial_hierarchy(ifc_store: IfcStore):
    sw = SpatialWriter(ifc_store)
    sw.create_ifc_site()


def write_ifc_part(ifc_store: IfcStore, part: Part, include_fem):
    from ada.api.spatial.equipment import Equipment
    from ada.cadit.ifc.write.write_equipment import write_ifc_equipment

    if isinstance(part, Equipment):
        return write_ifc_equipment(ifc_store, part)

    sw = SpatialWriter(ifc_store)

    ifc_part = sw.create_ifc_part(part)

    if len(part.fem.nodes) > 0 and include_fem is True:
        to_ifc_fem(part.fem, ifc_store.f)

    return ifc_part


@dataclass
class SpatialWriter:
    ifc_store: IfcStore

    def create_ifc_site(self):
        assembly = self.ifc_store.assembly

        f = self.ifc_store.f
        owner_history = self.ifc_store.owner_history

        site_placement = create_local_placement(f)
        site = f.create_entity(
            "IfcSite",
            GlobalId=assembly.guid,
            OwnerHistory=owner_history,
            Name=assembly.name,
            Description=None,
            ObjectPlacement=site_placement,
            CompositionType="ELEMENT",
        )
        f.create_entity(
            "IfcRelAggregates",
            create_guid(),
            owner_history,
            "Project Container",
            None,
            f.by_type("IfcProject")[0],
            [site],
        )

        write_elem_property_sets(assembly.metadata, site, f, owner_history)

        return site

    def create_ifc_part(self, part: Part):
        if part.parent is None:
            raise ValueError("Cannot build ifc element without parent")
        from ada.base.ifc_types import SpatialTypes as ITyp

        f = self.ifc_store.f

        owner_history = self.ifc_store.owner_history
        parent = self.ifc_store.get_by_guid(part.parent.guid)

        if part.ifc_class == ITyp.IfcBuildingStorey and parent.is_a() in ("IfcSite", "IfcProject"):
            # buildingSMART SPS002: a storey is aggregated by an IfcBuilding (or another storey),
            # never directly by the site.
            parent = self._get_or_create_implicit_building(parent)

        placement = create_local_placement(
            f,
            origin=part.placement.origin,
            loc_x=part.placement.xdir,
            loc_z=part.placement.zdir,
            relative_to=parent.ObjectPlacement,
        )

        from ada.cadit.ifc.write.entity_choice import resolve_ifc_entity

        choice = resolve_ifc_entity(part.metadata, f)
        if choice is not None:
            return self._create_ifc_element_part(part, choice, placement)

        ifc_class = part.ifc_class
        if parent.is_a("IfcElement") and ifc_class not in (ITyp.IfcElementAssembly, ITyp.IfcGrid):
            # A spatial element cannot decompose an element: a part inside one is an assembly.
            ifc_class = ITyp.IfcElementAssembly

        props = dict(
            GlobalId=part.guid,
            OwnerHistory=owner_history,
            Name=part.name,
            Description=part.metadata.get("Description", None),
            ObjectType=None,
            ObjectPlacement=placement,
            Representation=None,
        )

        if ifc_class not in (ITyp.IfcElementAssembly, ITyp.IfcGrid):
            props["LongName"] = part.metadata.get("LongName", None)

        if ifc_class not in (ITyp.IfcSpatialZone, ITyp.IfcElementAssembly, ITyp.IfcGrid):
            props["CompositionType"] = part.metadata.get("CompositionType", "ELEMENT")

        if ifc_class == ITyp.IfcBuildingStorey:
            props["Elevation"] = float(part.placement.origin[2])

        if ifc_class == ITyp.IfcGrid:
            from ada.cadit.ifc.utils import create_ifcpolyline

            # Todo: Create an IfcGrid object
            props["UAxes"] = [
                f.create_entity(
                    "IfcGridAxis",
                    AxisTag="XAxis",
                    AxisCurve=create_ifcpolyline(f, [(0, 0, 0), (1, 0, 0)]),
                    SameSense=True,
                )
            ]
            props["VAxes"] = [
                f.create_entity(
                    "IfcGridAxis",
                    AxisTag="YAxis",
                    AxisCurve=create_ifcpolyline(f, [(0, 0, 0), (0, 1, 0)]),
                    SameSense=True,
                )
            ]

        ifc_elem = f.create_entity(ifc_class.value, **props)

        existing_rel_agg = self.ifc_store.get_rel_aggregates(parent)
        if existing_rel_agg is not None:
            existing_rel_agg.RelatedObjects = tuple([*existing_rel_agg.RelatedObjects, ifc_elem])
        else:
            new_rel_agg = f.create_entity(
                "IfcRelAggregates",
                GlobalId=create_guid(),
                OwnerHistory=owner_history,
                Name="Site Container",
                Description=None,
                RelatingObject=parent,
                RelatedObjects=[ifc_elem],
            )
            self.ifc_store.register_rel_aggregates(parent, new_rel_agg)

        write_elem_property_sets(part.metadata, ifc_elem, f, owner_history)

        return ifc_elem

    def _aggregate(self, parent, children, name: str):
        existing_rel_agg = self.ifc_store.get_rel_aggregates(parent)
        if existing_rel_agg is not None:
            existing_rel_agg.RelatedObjects = tuple([*existing_rel_agg.RelatedObjects, *children])
            return
        new_rel_agg = self.ifc_store.f.create_entity(
            "IfcRelAggregates",
            GlobalId=create_guid(),
            OwnerHistory=self.ifc_store.owner_history,
            Name=name,
            Description=None,
            RelatingObject=parent,
            RelatedObjects=list(children),
        )
        self.ifc_store.register_rel_aggregates(parent, new_rel_agg)

    def _get_or_create_implicit_building(self, parent):
        """The IfcBuilding that stands between ``parent`` (an IfcSite/IfcProject) and the storeys
        written under it; one per parent, created on first use."""
        f = self.ifc_store.f
        existing_rel_agg = self.ifc_store.get_rel_aggregates(parent)
        if existing_rel_agg is not None:
            for obj in existing_rel_agg.RelatedObjects:
                if obj.is_a("IfcBuilding") and obj.ObjectType == IMPLICIT_BUILDING_TAG:
                    return obj

        building = f.create_entity(
            "IfcBuilding",
            GlobalId=create_guid(),
            OwnerHistory=self.ifc_store.owner_history,
            Name=f"{parent.Name or 'Site'} Building",
            ObjectType=IMPLICIT_BUILDING_TAG,
            # An IfcProject has no placement: the building is then placed in the world.
            ObjectPlacement=create_local_placement(f, relative_to=getattr(parent, "ObjectPlacement", None)),
            CompositionType="ELEMENT",
        )
        self._aggregate(parent, [building], "Site Container")
        return building

    def _create_ifc_element_part(self, part: Part, choice, placement):
        """``part`` as the element ``choice`` names: no body of its own, its contents aggregated
        into it, and itself contained in (or aggregated into) its parent."""
        f = self.ifc_store.f
        owner_history = self.ifc_store.owner_history
        ifc_elem = f.create_entity(
            choice.entity,
            GlobalId=part.guid,
            OwnerHistory=owner_history,
            Name=part.name,
            Description=part.metadata.get("Description", None),
            ObjectPlacement=placement,
            Representation=None,
            **choice.attributes(),
        )
        self.ifc_store.writer.add_related_elements_to_spatial_container([ifc_elem], part.parent.guid)
        write_elem_property_sets(part.metadata, ifc_elem, f, owner_history)
        return ifc_elem
