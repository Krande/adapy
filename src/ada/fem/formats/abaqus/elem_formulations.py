from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.fem.exceptions import IncompatibleElements

if TYPE_CHECKING:
    from ada.fem.shapes import definitions as shape_def


class AbaqusDefaultShellTypes:
    def __init__(self):
        self.TRIANGLE = "S3"
        self.TRIANGLE6 = "STRI65"
        self.TRIANGLE7 = "S7"
        self.QUAD = "S4"
        # Abaqus has no S8: its 8-node shells are S8R and S8R5, both reduced integration. S8
        # made every QUAD8 unwritable (refused as "full integration" below).
        self.QUAD8 = "S8R"


@dataclass
class AbaqusDefaultSolidTypes:
    HEXAHEDRON = "C3D8"
    HEXAHEDRON20 = "C3D20"
    HEXAHEDRON27 = "C3D27"
    TETRA = "C3D4"
    TETRA10 = "C3D10"
    PYRAMID5 = "C3D5"
    PRISM6 = "C3D6"
    PRISM15 = "C3D15"
    # SolidShapes calls these WEDGE / WEDGE15, and the lookup is by that value: without the
    # aliases every wedge was "Unrecognized element type".
    WEDGE = "C3D6"
    WEDGE15 = "C3D15"


@dataclass
class AbaqusDefaultLineTypes:
    LINE = "B31"
    LINE3 = "B32"


class AbaqusDefaultElemTypes:
    def __init__(self, is_calculix_variant: bool = False):
        self.LINE = AbaqusDefaultLineTypes()
        self.SHELL = AbaqusDefaultShellTypes()
        self.SOLID = AbaqusDefaultSolidTypes()
        self.use_reduced_integration = False
        self.is_calculix_variant = is_calculix_variant
        if is_calculix_variant:
            # Calculix, unlike Abaqus, has a fully integrated 8-node shell.
            self.SHELL.QUAD8 = "S8"

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

        if self.is_calculix_variant:
            if self.use_reduced_integration and res in (self.SHELL.TRIANGLE, self.SHELL.TRIANGLE6, "S6"):
                raise IncompatibleElements(f"Reduced integration is not supported for triangle elements {res}")
            if self.use_reduced_integration and res in (self.SOLID.PRISM6, self.SOLID.TETRA, self.SOLID.TETRA10):
                raise IncompatibleElements(f"Reduced integration is not supported for tetrahedral elements {res}")
        else:
            if res in (self.LINE.LINE, self.LINE.LINE3) and self.use_reduced_integration is True:
                raise IncompatibleElements(f"Reduced integration is not supported for line elements {res}")

            if self.use_reduced_integration:
                if res in (self.SOLID.TETRA10, self.SOLID.TETRA, self.SHELL.TRIANGLE6):
                    raise IncompatibleElements(f"Reduced integration is not supported for {res}")

        if self.use_reduced_integration and not res.endswith("R"):
            if self.is_calculix_variant:
                res += "R"
            else:
                # Only where Abaqus has one: appending R blindly wrote S7R, C3D5R, C3D6R and C3D15R,
                # element types that do not exist. The shape table the reader reads through is
                # the authority on what does.
                from .mapping import element_types

                if element_types().accepts(el_type, res + "R"):
                    res += "R"

        return res


class AbaqusPointTypes:
    spring1n = ["SPRING1"]
    masses = ["MASS", "ROTARYI"]

    all = [spring1n, masses]
