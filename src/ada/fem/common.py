from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ada.config import Config

if TYPE_CHECKING:
    from ada import FEM, Node
    from ada.fem.steps import Step


class FemBase:
    """Base class for all FEM objects

    Args:
        name (str): Name of the object
        metadata (dict, optional): Metadata for the object. Defaults to None.
        parent (FEM, optional): Parent FEM object. Defaults to None.
        str_override (str, optional): String representation of the object. Will override object writing. Defaults to None.
    """

    def __init__(self, name, metadata, parent: FEM | Step, str_override: str | None = None):
        self.name = name
        self.parent = parent
        self._metadata = metadata if metadata is not None else dict()
        self._str_override = str_override

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        from ada.core.utils import make_name_fem_ready

        if str.isnumeric(value[0]):
            raise ValueError("Name cannot start with numeric")

        if Config().general_convert_bad_names_for_fem:
            self._name = make_name_fem_ready(value)
        else:
            self._name = value.strip()

    @property
    def parent(self) -> FEM | Step:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def metadata(self) -> dict:
        return self._metadata

    @property
    def str_override(self) -> str | None:
        return self._str_override

    @str_override.setter
    def str_override(self, value):
        self._str_override = value


class CsysSystems:
    RECTANGULAR = "RECTANGULAR"
    # , 'CYLINDRICAL', 'SPHERICAL', 'Z RECTANGULAR', 'USER']


class CsysDefs:
    COORDINATES = "COORDINATES"
    NODES = "NODES"
    # ,'OFFSET TO NODES'


class Csys(FemBase):
    TYPES_SYSTEM = CsysSystems
    TYPES_DEFINITIONS = CsysDefs

    def __init__(
        self,
        name,
        definition=TYPES_DEFINITIONS.COORDINATES,
        system=TYPES_SYSTEM.RECTANGULAR,
        nodes: list[Node] = None,
        coords=None,
        metadata=None,
        parent: FEM = None,
    ):
        super().__init__(name, metadata, parent)
        self._definition = definition
        self._system = system
        if nodes is not None:
            for n in nodes:
                n.add_obj_to_refs(self)
        self._nodes = nodes
        self._coords = coords

    @property
    def definition(self):
        return self._definition

    @property
    def system(self):
        return self._system

    @property
    def nodes(self) -> list[Node]:
        return self._nodes

    @property
    def coords(self):
        """Coordinates: (x, y, origin[optional]). y can be anywhere in the x-y plane"""
        return self._coords

    @coords.setter
    def coords(self, value):
        self._coords = value

    def __repr__(self):
        content_map = dict(COORDINATES=self.coords, NODES=self.nodes)
        return f'Csys("{self.name}", "{self.definition}", {content_map[self.definition]})'


class Amplitude(FemBase):
    def __init__(self, name: str, x: list[float], y: list[float], smooth=None, metadata=None, parent: FEM = None):
        super().__init__(name, metadata, parent)
        self._x = x
        self._y = y
        self._smooth = smooth

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def smooth(self):
        return self._smooth


@dataclass
class LinDep:
    master: np.ndarray
    slave: np.ndarray

    Xdict: dict = None
    Ydict: dict = None
    Zdict: dict = None

    def __post_init__(self):
        diff = self.slave - self.master
        dx, dy, dz = diff

        self.X = dict(
            x=1,
            xR_z=-dy,
            xR_y=dz,
        )
        self.Y = dict(
            y=1,
            yR_z=dx,
            yR_x=-dz,
        )
        self.Z = dict(
            z=1,
            zR_x=dy,
            zR_y=-dx,
        )

    def to_integer_list(self):
        return [
            (1, 1, self.X["x"]),
            (1, 5, self.X["xR_y"]),
            (1, 6, self.X["xR_z"]),
            (2, 2, self.Y["y"]),
            (2, 4, self.Y["yR_x"]),
            (2, 6, self.Y["yR_z"]),
            (3, 3, self.Z["z"]),
            (3, 4, self.Z["zR_x"]),
            (3, 5, self.Z["zR_y"]),
        ]
