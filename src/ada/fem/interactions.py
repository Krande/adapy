from typing import List, Union

from .common import FemBase
from .constraints import Constraint
from .surfaces import Surface


class IntPropTypes:
    HARD = "HARD"
    TABULAR = "TABULAR"
    PENALTY = "PENALTY"
    all = [HARD, TABULAR, PENALTY]


class ContactTypes:
    SURFACE = "SURFACE"
    GENERAL = "GENERAL"
    all = [SURFACE, GENERAL]


class SurfTypes:
    SURF2SURF = "SURFACE TO SURFACE"
    all = [SURF2SURF]


class InteractionProperty(FemBase):
    def __init__(
        self,
        name,
        friction=None,
        pressure_overclosure=IntPropTypes.HARD,
        tabular=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._friction = friction if friction is not None else 0.0
        self._pressure_overclosure = pressure_overclosure
        if self.pressure_overclosure.upper() not in IntPropTypes.all:
            raise ValueError(f'Pressure overclosure type "{pressure_overclosure}" is not supported')
        self._tabular = tabular

    @property
    def friction(self):
        return self._friction

    @property
    def pressure_overclosure(self):
        return self._pressure_overclosure.strip()

    @property
    def tabular(self):
        return self._tabular

    @tabular.setter
    def tabular(self, value: List[tuple]):
        self._tabular = value


class Interaction(FemBase):
    """A class representing the physical properties of interaction between solid bodies."""

    def __init__(
        self,
        name,
        contact_type,
        surf1: Union[Surface, None],
        surf2: Union[Surface, None],
        int_prop: InteractionProperty,
        constraint: Constraint = None,
        surface_type=SurfTypes.SURF2SURF,
        parent=None,
        metadata=None,
    ):
        super().__init__(name, metadata, parent)

        self.type = contact_type
        self.surface_type = surface_type
        self._surf1 = surf1
        self._surf2 = surf2
        self._int_prop = int_prop
        self._constraint = constraint

    @property
    def parent(self):
        """:rtype: ada.FEM"""
        return self._parent

    @parent.setter
    def parent(self, value):
        from ada import FEM
        from ada.fem.steps import Step

        if type(value) is not FEM and value is not None and issubclass(type(value), Step) is False:
            raise ValueError(f'Parent type "{type(value)}" is not supported')
        self._parent = value

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.upper() not in ContactTypes.all:
            raise ValueError(f'Contact type cannot be "{value}". Must be in {ContactTypes.all}')
        self._type = value.upper()

    @property
    def surf1(self) -> Surface:
        return self._surf1

    @property
    def surf2(self) -> Surface:
        return self._surf2

    @property
    def interaction_property(self) -> InteractionProperty:
        return self._int_prop

    @property
    def constraint(self) -> Constraint:
        return self._constraint

    @property
    def surface_type(self):
        return self._surface_type

    @surface_type.setter
    def surface_type(self, value):
        if value not in SurfTypes.all:
            raise ValueError(f'Surface type cannot be "{value}". Must be in {SurfTypes.all}')
        self._surface_type = value
