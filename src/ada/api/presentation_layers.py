from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.base.changes import ChangeAction
from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada.base.physical_objects import BackendGeom


@dataclass
class PresentationLayer:
    name: str
    description: str
    members: list[BackendGeom] = field(default_factory=list)
    change_type: ChangeAction = ChangeAction.NOTDEFINED
    identifier: str = field(default_factory=create_guid)


@dataclass
class PresentationLayers:
    layers: dict[str, PresentationLayer] = field(default_factory=dict)

    def add_layer(self, layer: str | PresentationLayer, description: str = None) -> PresentationLayer:
        if isinstance(layer, PresentationLayer):
            existing_layer = self.layers.get(layer.name)
        else:
            existing_layer = self.layers.get(layer)

        if existing_layer is not None:
            raise ValueError(f'Existing Layer with name="{layer}": {existing_layer}')

        if isinstance(layer, PresentationLayer):
            new_layer = layer
        else:
            new_layer = PresentationLayer(layer, description=description, change_type=ChangeAction.ADDED)

        self.layers[layer] = new_layer

        return new_layer

    def get_by_name(self, layer_name) -> None | PresentationLayer:
        return self.layers.get(layer_name)

    def add_object(self, obj, layer: str):
        from ada import Part

        layer_obj = self.get_by_name(layer)
        if layer_obj is None:
            logger.info(f'Layer "{layer}" does not exist. So creating a new layer')
            layer_obj = self.add_layer(layer)
        else:
            layer_obj.change_type = ChangeAction.MODIFIED

        if isinstance(obj, Part):
            for geom in obj.get_all_physical_objects():
                layer_obj.members.append(geom)
        else:
            layer_obj.members.append(obj)

    def remove_layer_and_delete_objects(self, layer: str):
        layer_obj = self.layers.get(layer)
        for mem in layer_obj.members:
            mem.change_type = ChangeAction.DELETED

        self.layers.pop(layer)
