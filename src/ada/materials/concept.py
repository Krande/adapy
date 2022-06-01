from __future__ import annotations

from typing import TYPE_CHECKING

from ada.base.non_physical_objects import Backend

from .metals import CarbonSteel

if TYPE_CHECKING:
    from ada.ifc.concepts import IfcRef


class Material(Backend):
    """The base material class. Currently only supports Metals"""

    def __init__(
        self,
        name,
        mat_model=CarbonSteel("S355"),
        mat_id=None,
        parent=None,
        metadata=None,
        units="m",
        guid=None,
        ifc_ref: IfcRef = None,
    ):
        super(Material, self).__init__(name, guid, metadata, units, ifc_ref=ifc_ref)
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

    def equal_props(self, other: Material):
        self.model.__eq__()

    def __hash__(self):
        return hash(self.guid)

    def _generate_ifc_mat(self):
        from ada.ifc.write.write_material import write_ifc_mat

        return write_ifc_mat(self)

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
        if value is None or any(x in value for x in [",", ".", "="]):
            raise ValueError("Material name cannot be None or contain special characters")

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
        self.model.units = value

    @property
    def refs(self):
        return self._refs

    @property
    def ifc_mat(self):
        if self._ifc_mat is None:
            self._ifc_mat = self._generate_ifc_mat()
        return self._ifc_mat

    def __repr__(self):
        return f'Material(Name: "{self.name}" Material Model: "{self.model}'
