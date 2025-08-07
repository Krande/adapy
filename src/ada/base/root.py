from __future__ import annotations

from typing import TYPE_CHECKING, Union

from ada.config import Config, logger

from ..core.guid import create_guid
from .changes import ChangeAction
from .units import Units

if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.cadit.ifc.store import IfcStore


class Root:
    UNITS: Units = Units

    def __init__(
        self,
        name,
        guid=None,
        metadata=None,
        units: Units | str = Units.M,
        parent=None,
        ifc_store: IfcStore = None,
        change_type: ChangeAction = ChangeAction.ADDED,
    ):
        self.name = name
        self.parent = parent
        self.change_type = change_type
        self.guid = create_guid() if guid is None else guid

        if isinstance(units, str):
            units = Units.from_str(units)

        self._units = units
        self._metadata = metadata if metadata is not None else dict()
        self._ifc_store = ifc_store

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if Config().general_convert_bad_names:
            logger.debug("Converting bad name")
            value = value.replace("/", "_").replace("=", "")
            if str.isnumeric(value[0]):
                value = "ADA_" + value

        if "/" in value:
            logger.debug(f'Character "/" found in {value}')

        self._name = value.strip()

    @property
    def guid(self):
        return self._guid

    @guid.setter
    def guid(self, value):
        if value is None:
            raise ValueError("guid cannot be None")
        if hasattr(self, "_guid") and self.guid is not None:
            existing_guid = self.guid
            a = self.get_assembly()
            if a._ifc_store is not None:
                original_el = a.ifc_store.f.by_guid(existing_guid)
                original_el.GlobalId = value
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

    @metadata.setter
    def metadata(self, value):
        self._metadata = value

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        raise NotImplementedError("Assigning units is not yet represented for this object")

    def get_assembly(self) -> Union[Assembly, Part]:
        from ada import Assembly

        for ancestor in self.get_ancestors():
            if isinstance(ancestor, Assembly):
                return ancestor
        logger.info("No Assembly found in ancestry. Returning self")
        return self

    def get_ancestors(self, include_self=True) -> list[Part | Assembly]:
        ancestry = [self] if include_self else []
        current = self
        while current.parent is not None:
            ancestry.append(current.parent)
            current = current.parent
        return ancestry

    def remove(self):
        """Remove this element/part from assembly/part"""
        from ada import Beam, Part, Plate, Section, Shape

        if self.parent is None:
            logger.error(f"Unable to delete {self.name} as it does not have a parent")
            return

        if issubclass(type(self), Part):
            self.parent.parts.pop(self.name)
        elif issubclass(type(self), Shape):
            self.parent.shapes.pop(self.parent.shapes.index(self))
        elif isinstance(self, Beam):
            self.parent.beams.remove(self)
        elif isinstance(self, Plate):
            self.parent.plates.remove(self)
        elif isinstance(self, Section):
            logger.warning("Section removal is not yet supported")
        else:
            raise NotImplementedError()
