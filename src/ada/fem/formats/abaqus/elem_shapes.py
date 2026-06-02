from __future__ import annotations

from ada.fem.shapes.definitions import (
    ConnectorTypes,
    LineShapes,
    MassTypes,
    ShellShapes,
    SolidShapes,
)

sh = ShellShapes
so = SolidShapes
li = LineShapes


ada_to_abaqus_format = {
    # Shell / membrane / plane-stress / plane-strain families that
    # share the underlying topology (3-node tri vs 4-node quad). For
    # visualization + topology-level conversion to other FEA solvers
    # we treat them as the equivalent ada shape; the per-element
    # stress / membrane / plane-stress formulation is solver
    # metadata we don't try to round-trip in the topology mapping.
    # CPS* = plane stress, CPE* = plane strain, CAX* = axisymmetric,
    # M3D* = membrane.
    sh.TRI: (
        "S3",
        "S3R",
        "R3D3",
        "S3RS",
        "CPS3",
        "CPE3",
        "CAX3",
        "M3D3",
    ),
    sh.TRI6: ("STRI65", "CPS6", "CPE6", "CAX6", "M3D6"),
    sh.TRI7: ("S7",),
    sh.QUAD: (
        "S4",
        "S4R",
        "R3D4",
        "CPS4",
        "CPS4R",
        "CPE4",
        "CPE4R",
        "CAX4",
        "CAX4R",
        "M3D4",
        "M3D4R",
    ),
    sh.QUAD8: (
        "S8",
        "S8R",
        "CPS8",
        "CPS8R",
        "CPE8",
        "CPE8R",
        "CAX8",
        "CAX8R",
        "M3D8",
        "M3D8R",
    ),
    so.HEX8: ("C3D8", "C3D8R", "C3D8H"),
    so.HEX20: ("C3D20", "C3D20R", "C3D20RH"),
    so.HEX27: ("C3D27",),
    so.TETRA: ("C3D4",),
    so.TETRA10: ("C3D10",),
    so.PYRAMID5: ("C3D5", "C3D5H"),
    so.WEDGE: ("C3D6",),
    so.WEDGE15: ("C3D15",),
    li.LINE: ("B31", "B31H"),
    li.LINE3: ("B32",),
    MassTypes.MASS: ("MASS",),
    MassTypes.ROTARYI: ("ROTARYI",),
    MassTypes.NONSTRUCTURAL: ("NONSTRUCTURAL MASS",),
    ConnectorTypes.CONNECTOR: ("CONN3D2",),
}


class UnsupportedAbaqusElementType(ValueError):
    """Raised by :func:`abaqus_el_type_to_ada` when we don't know how
    to map an Abaqus element type to one of adapy's canonical shapes.
    Distinct subclass so callers can skip-and-continue on user-
    defined element types (``U1``, ``U2``, …) — those depend on the
    deck's ``*USER ELEMENT`` definition which we don't parse, so the
    only honest answer is "topology unknown, skip this element block
    rather than abort the whole file"."""


def abaqus_el_type_to_ada(el_type):
    for key, val in ada_to_abaqus_format.items():
        if el_type in val:
            return key
    raise UnsupportedAbaqusElementType(f'Element type "{el_type}" has not been added to conversion to ada map yet')
