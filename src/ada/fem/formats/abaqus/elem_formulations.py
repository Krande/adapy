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


# ---- section theories ------------------------------------------------------------------------------------------
#: The Abaqus element type of each shape for a section whose theory is not the default one (Abaqus Analysis User's
#: Guide, "Membrane elements" and "Truss elements"); Calculix has the same names. A shape missing here has no element
#: of that theory.
THEORY_ELEMENT_TYPES = {
    "membrane": {"TRIANGLE": "M3D3", "TRIANGLE6": "M3D6", "QUAD": "M3D4", "QUAD8": "M3D8"},
    "truss": {"LINE": "T3D2", "LINE3": "T3D3"},
    "cable": {"LINE": "T3D2", "LINE3": "T3D3"},
}
#: The reduced-integration form of a membrane type, where one exists.
REDUCED_MEMBRANES = {"M3D4": "M3D4R", "M3D8": "M3D8R"}
#: The leading letters of every element type of a theory. A source formulation of another theory's family is not
#: written on a section of this one: an S4R read from a deck is not a membrane, an M3D4 not a bending shell.
_THEORY_FAMILIES = {"membrane": ("M3D",), "truss": ("T3D",), "cable": ("T3D",)}
_SPECIAL_FAMILIES = ("M3D", "T3D")


def section_theory(elem) -> str | None:
    """The value of the theory of ``elem``'s section (``"membrane"``, ``"truss"``, ...), or None without one."""
    fem_sec = getattr(elem, "fem_sec", None)
    theory = getattr(fem_sec, "theory", None)
    return None if theory is None else theory.value


def is_default_theory(theory: str | None) -> bool:
    return theory in (None, "shell", "beam")


def theory_element_type(theory: str, shape, use_reduced_integration: bool = False) -> str:
    """The Abaqus type of a ``shape`` element on a section of ``theory``; raises when there is none."""
    name = THEORY_ELEMENT_TYPES[theory].get(shape.value.upper())
    if name is None:
        raise IncompatibleElements(f"Abaqus has no {theory} element of shape {shape}")
    if use_reduced_integration:
        name = REDUCED_MEMBRANES.get(name, name)
    return name


def theory_accepts(theory: str | None, name: str) -> bool:
    """Whether an element type ``name`` belongs to a section of ``theory``."""
    name = str(name).upper()
    if is_default_theory(theory):
        return not name.startswith(_SPECIAL_FAMILIES)
    return name.startswith(_THEORY_FAMILIES[theory])
