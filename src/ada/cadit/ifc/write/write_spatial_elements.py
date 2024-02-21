from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import create_local_placement, write_elem_property_sets
from ada.core.guid import create_guid
from ada.fem.formats.ifc.writer import to_ifc_fem

if TYPE_CHECKING:
    from ada import Part
    from ada.cadit.ifc.store import IfcStore


def write_ifc_spatial_hierarchy(ifc_store: IfcStore):
    sw = SpatialWriter(ifc_store)
    sw.create_ifc_site()


def write_ifc_part(ifc_store: IfcStore, part: Part, include_fem):
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

        placement = create_local_placement(
            f,
            origin=part.placement.origin,
            loc_x=part.placement.xdir,
            loc_z=part.placement.zdir,
            relative_to=parent.ObjectPlacement,
        )

        props = dict(
            GlobalId=part.guid,
            OwnerHistory=owner_history,
            Name=part.name,
            Description=part.metadata.get("Description", None),
            ObjectType=None,
            ObjectPlacement=placement,
            Representation=None,
        )

        if part.ifc_class not in (ITyp.IfcElementAssembly, ITyp.IfcGrid):
            props["LongName"] = part.metadata.get("LongName", None)

        if part.ifc_class not in (ITyp.IfcSpatialZone, ITyp.IfcElementAssembly, ITyp.IfcGrid):
            props["CompositionType"] = part.metadata.get("CompositionType", "ELEMENT")

        if part.ifc_class == ITyp.IfcBuildingStorey:
            props["Elevation"] = float(part.placement.origin[2])

        if part.ifc_class == ITyp.IfcGrid:
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

        ifc_elem = f.create_entity(part.ifc_class.value, **props)

        existing_rel_agg = False
        for rel_agg in f.by_type("IfcRelAggregates"):
            if rel_agg.RelatingObject == parent:
                rel_agg.RelatedObjects = tuple([*rel_agg.RelatedObjects, ifc_elem])
                existing_rel_agg = True
                break

        if existing_rel_agg is False:
            f.create_entity(
                "IfcRelAggregates",
                GlobalId=create_guid(),
                OwnerHistory=owner_history,
                Name="Site Container",
                Description=None,
                RelatingObject=parent,
                RelatedObjects=[ifc_elem],
            )

        write_elem_property_sets(part.metadata, ifc_elem, f, owner_history)

        return ifc_elem
