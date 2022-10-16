from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.shapes import definitions as shape_def


class AbaqusDefaultShellTypes:
    def __init__(self):
        self.TRIANGLE = "S3"
        self.TRIANGLE6 = "STRI65"
        self.TRIANGLE7 = "S7"
        self.QUAD = "S4R"
        self.QUAD8 = "S8R"


@dataclass
class AbaqusDefaultSolidTypes:
    HEXAHEDRON = "C3D8"
    HEXAHEDRON20 = "C3D20R"
    HEXAHEDRON27 = "C3D27"
    TETRA = "C3D4"
    TETRA10 = "C3D10"
    PYRAMID5 = "C3D5"
    PRISM6 = "C3D6"
    PRISM15 = "C3D15"


@dataclass
class AbaqusDefaultLineTypes:
    LINE = "B31"
    LINE3 = "B32"


class AbaqusDefaultElemTypes:
    def __init__(self):
        self.LINE = AbaqusDefaultLineTypes()
        self.SHELL = AbaqusDefaultShellTypes()
        self.SOLID = AbaqusDefaultSolidTypes()

    def get_element_type(self, el_type: shape_def.LineShapes | shape_def.ShellShapes | shape_def.SolidShapes) -> str:
        from ada.fem.shapes import ElemType
        from ada.fem.shapes.definitions import ConnectorTypes, MassTypes, ShapeResolver

        if isinstance(el_type, (MassTypes, ConnectorTypes)):
            return str(el_type.value)

        type_group = ShapeResolver.to_geom_repr(el_type)

        type_map = {
            ElemType.LINE: self.LINE,
            ElemType.SHELL: self.SHELL,
            ElemType.SOLID: self.SOLID,
        }

        res = getattr(type_map[type_group], el_type.value.upper(), None)

        if res is None:
            raise ValueError(f'Unrecognized element type "{el_type}"')

        return res


class AbaqusPointTypes:
    spring1n = ["SPRING1"]
    masses = ["MASS", "ROTARYI"]

    all = [spring1n, masses]
