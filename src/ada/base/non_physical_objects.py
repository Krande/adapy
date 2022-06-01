from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Union

from ada.config import Settings as _Settings
from ada.ifc.utils import create_guid

if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.ifc.concepts import IfcRef


@dataclass
class IfcExportOptions:
    export_props: bool = field(default=True)


class Backend:
    def __init__(
        self,
        name,
        guid=None,
        metadata=None,
        units="m",
        parent=None,
        ifc_settings=None,
        ifc_elem=None,
        ifc_ref: IfcRef = None,
    ):
        self.name = name
        self.parent = parent
        self._ifc_settings = ifc_settings
        self.guid = create_guid() if guid is None else guid
        units = units.lower()
        if units not in _Settings.valid_units:
            raise ValueError(f'Unit type "{units}"')
        self._units = units
        self._metadata = metadata if metadata is not None else dict(props=dict())
        self._ifc_elem = ifc_elem
        # TODO: Currently not able to keep and edit imported ifc_elem objects
        self._ifc_elem = None
        self._ifc_ref = ifc_ref
        self.ifc_options: IfcExportOptions = IfcExportOptions()

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if _Settings.convert_bad_names:
            logging.debug("Converting bad name")
            value = value.replace("/", "_").replace("=", "")
            if str.isnumeric(value[0]):
                value = "ADA_" + value

        if "/" in value:
            logging.debug(f'Character "/" found in {value}')

        self._name = value.strip()

    @property
    def guid(self):
        return self._guid

    @guid.setter
    def guid(self, value):
        if value is None:
            raise ValueError("guid cannot be None")
        self._guid = value

    @property
    def parent(self) -> Part:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def metadata(self):
        return self._metadata

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        raise NotImplementedError("Assigning units is not yet represented for this object")

    @property
    def ifc_settings(self):
        if self._ifc_settings is None:
            from ada.ifc.utils import default_settings

            self._ifc_settings = default_settings()
        return self._ifc_settings

    @ifc_settings.setter
    def ifc_settings(self, value):
        self._ifc_settings = value

    def get_ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

    @property
    def ifc_ref(self) -> IfcRef:
        return self._ifc_ref

    def get_assembly(self) -> Union[Assembly, Part]:
        from ada import Assembly

        for ancestor in self.get_ancestors():
            if isinstance(ancestor, Assembly):
                return ancestor
        logging.info("No Assembly found in ancestry. Returning self")
        return self

    def get_ancestors(self) -> List[Union[Part, Assembly]]:
        ancestry = [self]
        current = self
        while current.parent is not None:
            ancestry.append(current.parent)
            current = current.parent
        return ancestry

    def _generate_ifc_elem(self):
        raise NotImplementedError("")

    def remove(self):
        """Remove this element/part from assembly/part"""
        from ada import Beam, Part, Plate, Shape

        if self.parent is None:
            logging.error(f"Unable to delete {self.name} as it does not have a parent")
            return

        # if self._ifc_elem is not None:
        #     a = self.parent.get_assembly()
        # f = a.ifc_file
        # This returns results in a failure error
        # f.remove(self.ifc_elem)

        if type(self) is Part:
            self.parent.parts.pop(self.name)
        elif issubclass(type(self), Shape):
            self.parent.shapes.pop(self.parent.shapes.index(self))
        elif type(self) is Beam:
            self.parent.beams.remove(self)
        elif isinstance(self, Plate):
            self.parent.plates.remove(self)
        else:
            raise NotImplementedError()
