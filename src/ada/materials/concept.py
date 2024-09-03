from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from ada.base.root import Root
from ada.base.units import Units

from .metals import CarbonSteel

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


class Material(Root):
    """The base material class. Currently only supports Metals"""

    def __init__(
        self,
        name,
        mat_model=CarbonSteel("S355"),
        mat_id=None,
        parent=None,
        metadata=None,
        units=Units.M,
        guid=None,
        ifc_store: IfcStore = None,
    ):
        super(Material, self).__init__(name, guid, metadata, units, ifc_store=ifc_store)
        self._mat_model = mat_model
        mat_model.parent = self
        self._mat_id = mat_id
        self._ifc_mat = None
        self._parent = parent
        self._refs = []

    def __eq__(self, other: Material):
        """
        Assuming uniqueness of Material Name and parent

        TODO: Make this check for same Material Model parameters

        :param other:
        :return:
        """
        # other_parent = other.__dict__['_parent']
        # other_name = other.__dict__['_name']
        # if self.name == other_name and other_parent == self.parent:
        #     return True
        # else:
        #     return False

        for key, val in self.__dict__.items():
            if "parent" in key or key == "_mat_id":
                continue
            if other.__dict__[key] != val:
                return False

        return True

    def __hash__(self):
        return hash(self.guid)

    def copy_to(self, new_name: str, parent=None) -> Material:
        og_parent = self.model.parent
        self.model.parent = None
        new_mat = Material(new_name, mat_model=copy.deepcopy(self.model), parent=parent)
        self.model.parent = og_parent
        return new_mat

    @property
    def id(self):
        return self._mat_id

    @id.setter
    def id(self, value):
        self._mat_id = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        # if value is None or any(x in value for x in [",", ".", "="]):
        #     raise ValueError(f"Material name {value} cannot be None or contain special characters")

        self._name = value.strip()

    @property
    def model(self) -> CarbonSteel:
        return self._mat_model

    @model.setter
    def model(self, value):
        value.parent = self
        self._mat_model = value

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        self.model.units = value

    @property
    def refs(self):
        return self._refs

    def __repr__(self):
        return f'Material(Name: "{self.name}" Material Model: "{self.model}'
