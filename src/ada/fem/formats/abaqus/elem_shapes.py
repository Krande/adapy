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
    sh.TRI: ("S3", "S3R", "R3D3", "S3RS"),
    sh.TRI6: ("STRI65",),
    sh.TRI7: ("S7",),
    sh.QUAD: ("S4", "S4R", "R3D4"),
    sh.QUAD8: ("S8", "S8R"),
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
    ConnectorTypes.CONNECTOR: ("CONN3D2",),
}


def abaqus_el_type_to_ada(el_type):
    for key, val in ada_to_abaqus_format.items():
        if el_type in val:
            return key
    raise ValueError(f'Element type "{el_type}" has not been added to conversion to ada map yet')
