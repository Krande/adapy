from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.base.changes import ChangeAction
from ada.cadit.ifc.read.read_physical_objects import import_physical_ifc_elem
from ada.cadit.ifc.read.reader_utils import (
    add_to_assembly,
    get_ifc_property_sets,
    get_parent,
    resolve_name,
)
from ada.config import logger

from .read_materials import MaterialImporter
from .read_parts import PartImporter

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


@dataclass
class IfcReader:
    ifc_store: IfcStore

    def load_spatial_hierarchy(self):
        pi = PartImporter(self.ifc_store)
        pi.load_hierarchies()

    def load_materials(self):
        mi = MaterialImporter(self.ifc_store)
        mi.load_ifc_materials()

    def load_presentation_layers(self):
        from ada.api.presentation_layers import PresentationLayer, PresentationLayers

        layers = dict()
        for obj in self.ifc_store.f.by_type("IfcPresentationLayerAssignment"):
            members = []
            for x in obj.AssignedItems:
                guid = None
                for inverse in self.ifc_store.f.get_inverse(x):
                    if inverse.is_a("IfcShapeRepresentation"):
                        product = inverse.OfProductRepresentation[0].ShapeOfProduct[0]
                        guid = product.GlobalId
                        break
                elem = self.ifc_store.assembly.get_by_guid(guid)
                if elem is None:
                    # raise ValueError()
                    continue
                if elem not in members:
                    members.append(elem)

            pl = PresentationLayer(
                obj.Name, obj.Description, members, identifier=obj.Identifier, change_type=ChangeAction.NOCHANGE
            )
            layers[pl.name] = pl

        self.ifc_store.assembly.presentation_layers = PresentationLayers(layers)

    def load_objects(self, data_only=False, elements2part=None):
        for product in self.ifc_store.f.by_type("IfcProduct"):
            if product.Representation is None or data_only is True:
                logger.info(f'Passing product "{product}"')
                continue

            parent = get_parent(product)
            name = product.Name

            props = get_ifc_property_sets(product)

            if name is None:
                name = resolve_name(props, product)

            logger.info(f"importing {name}")

            obj = import_physical_ifc_elem(product, name, self.ifc_store)
            if obj is None:
                continue

            obj.metadata = props

            add_to_assembly(self.ifc_store.assembly, obj, parent, elements2part)
