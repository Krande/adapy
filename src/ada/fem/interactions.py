from .common import FemBase
from .steps import Step


class InteractionProperty(FemBase):
    """

    :param name:
    :param friction:
    :param pressure_overclosure:
    :param tabular:
    :param metadata:
    :param parent:
    """

    _valid_po = ["HARD", "TABULAR", "PENALTY"]

    def __init__(
        self,
        name,
        friction=None,
        pressure_overclosure="HARD",
        tabular=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._friction = friction if friction is not None else 0.0
        self._pressure_overclosure = pressure_overclosure
        if self.pressure_overclosure not in InteractionProperty._valid_po:
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


class Interaction(FemBase):
    """
    A class representing the physical properties of
    interaction between solid bodies.

    """

    _valid_contact_types = ["SURFACE", "GENERAL"]
    _valid_surface_types = ["SURFACE TO SURFACE"]

    def __init__(
        self,
        name,
        contact_type,
        surf1,
        surf2,
        int_prop,
        constraint=None,
        surface_type="SURFACE TO SURFACE",
        parent=None,
        metadata=None,
    ):
        """

        :param name:
        :param surf1:
        :param surf2:
        :param int_prop:
        :param constraint:
        :param surface_type: Interaction type.
        :type name: str
        :type int_prop: InteractionProperty
        :type constraint: str
        :type surf1: Surface
        :type surf2: Surface
        :type surface_type: str
        :type parent: FEM
        :type metadata: dict
        """
        super().__init__(name, metadata, parent)

        self.type = contact_type
        self.surface_type = surface_type
        self._surf1 = surf1
        self._surf2 = surf2
        self._int_prop = int_prop
        self._constraint = constraint

    @property
    def parent(self):
        """

        :rtype: ada.fem.FEM
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        from . import FEM

        if type(value) not in (FEM, Step) and value is not None:
            raise ValueError(f'Parent type "{type(value)}" is not supported')
        self._parent = value

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.upper() not in self._valid_contact_types:
            raise ValueError(f'Contact type cannot be "{value}". Must be in {self._valid_contact_types}')
        self._type = value.upper()

    @property
    def surf1(self):
        """

        :rtype: Surface
        """
        return self._surf1

    @property
    def surf2(self):
        """

        :rtype: Surface
        """
        return self._surf2

    @property
    def interaction_property(self):
        """

        :return:
        :rtype: InteractionProperty
        """
        return self._int_prop

    @property
    def constraint(self):
        return self._constraint

    @property
    def surface_type(self):
        return self._surface_type

    @surface_type.setter
    def surface_type(self, value):
        if value not in self._valid_surface_types:
            raise ValueError(f'Surface type cannot be "{value}". Must be in {self._valid_surface_types}')
        self._surface_type = value
