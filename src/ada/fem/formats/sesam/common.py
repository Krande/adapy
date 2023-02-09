from __future__ import annotations

from ada.fem.shapes.definitions import (
    LineShapes,
    MassTypes,
    ShellShapes,
    SolidShapes,
    SpringTypes,
)

sesam_el_map = {
    15: LineShapes.LINE,
    2: LineShapes.LINE,
    23: LineShapes.LINE3,
    24: ShellShapes.QUAD,
    25: ShellShapes.TRI,
    26: ShellShapes.TRI6,
    28: ShellShapes.QUAD8,
    31: SolidShapes.TETRA10,
    40: SpringTypes.SPRING2,
    18: SpringTypes.SPRING1,
    11: MassTypes.MASS,
}

sesam_reverse = {value: key for key, value in sesam_el_map.items()}


def sesam_eltype_2_general(eltyp: int) -> LineShapes | ShellShapes | SolidShapes | SpringTypes | MassTypes:
    """Converts the numeric definition of elements in Sesam to a generalized element type form (ie. B31, S4, etc..)"""
    res = sesam_el_map.get(eltyp, None)
    if res is None:
        raise Exception("Currently unsupported eltype", eltyp)
    return res
