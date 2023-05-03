from __future__ import annotations

from dataclasses import dataclass

import ifcopenshell
from ifcopenshell.util.element import get_psets

from ada import Assembly, Part
from ada.base.ifc_types import SpatialTypes
from ada.cadit.ifc.store import IfcStore
from ada.config import logger

from .reader_utils import get_ifc_property_sets, get_parent, resolve_name


def valid_spatial_classes(product: ifcopenshell.entity_instance):
    is_ok_class = SpatialTypes.is_valid_spatial_type(product.is_a())
    has_no_geom = product.Representation is None

    if is_ok_class is True and has_no_geom is True:
        return True

    if is_ok_class is True:
        logger.info(f"{product=}-> {has_no_geom=}")
        return True

    return False


@dataclass
class PartImporter:
    ifc_store: IfcStore

    def get_parent(self, product: ifcopenshell.entity_instance) -> Part | Assembly | None:
        pp = get_parent(product)
        pp_name = pp.Name
        if pp_name is None:
            pp_name = resolve_name(get_psets(pp), pp)
        if pp_name is None:
            return None
        return self.ifc_store.assembly.get_by_name(pp_name)

    def load_hierarchies(self) -> None:
        for product in filter(valid_spatial_classes, self.ifc_store.f.by_type("IfcProduct")):
            class_type = SpatialTypes.from_str(product.is_a())
            if class_type == SpatialTypes.IfcSite:
                self.update_assembly(product)
                continue
            new_part = self.import_ifc_hierarchy(product)
            parent = self.get_parent(product)
            if parent is None:
                if new_part.name not in self.ifc_store.assembly.parts.keys():
                    self.ifc_store.assembly.add_part(new_part)
            elif isinstance(parent, Part) is False:
                raise NotImplementedError()
            else:
                parent.add_part(new_part)

    def import_ifc_hierarchy(self, product: ifcopenshell.entity_instance) -> Part:
        props = get_ifc_property_sets(product)
        name = product.Name
        if name is None:
            logger.debug(f'Name was not found for the IFC element "{product}". Will look for ref to name in props')
            name = resolve_name(props, product)

        ifc_class = SpatialTypes.from_str(product.is_a())

        return Part(
            name,
            metadata=props,
            guid=product.GlobalId,
            ifc_store=self.ifc_store,
            units=self.ifc_store.assembly.units,
            ifc_class=ifc_class,
        )

    def update_assembly(self, product: ifcopenshell.entity_instance):
        props = get_ifc_property_sets(product)
        name = product.Name
        if name is None:
            logger.debug(f'Name was not found for the IFC element "{product}". Will look for ref to name in props')
            name = resolve_name(props, product)

        self.ifc_store.assembly.name = name
        self.ifc_store.assembly._guid = product.GlobalId
        self.ifc_store.assembly.metadata.update(props)
