from __future__ import annotations

import pathlib
from typing import Union

from ada import fem
from ada.concepts.connections import Bolts, Weld
from ada.concepts.curves import ArcSegment, CurvePoly, CurveRevolve, LineSegment
from ada.concepts.levels import FEM, Assembly, Part
from ada.concepts.piping import Pipe, PipeSegElbow, PipeSegStraight
from ada.concepts.points import Node
from ada.concepts.primitives import (
    Penetration,
    PrimBox,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.concepts.structural import Beam, Plate, Wall
from ada.concepts.transforms import Placement
from ada.config import User
from ada.materials import Material
from ada.sections import Section

__author__ = "Kristoffer H. Andersen"


def from_ifc(ifc_file: Union[str, pathlib.Path]) -> Assembly:
    a = Assembly()
    a.read_ifc(ifc_file)
    return a


def from_step(step_file: Union[str, pathlib.Path], source_units="m", **kwargs) -> Assembly:
    a = Assembly()
    a.read_step_file(step_file, source_units=source_units, **kwargs)
    return a


def from_fem(
    fem_file: Union[str, list, pathlib.Path],
    fem_format: Union[str, list] = None,
    name: Union[str, list] = None,
    enable_experimental_cache=False,
    source_units="m",
    fem_converter="default",
) -> Assembly:
    a = Assembly(enable_experimental_cache=enable_experimental_cache, units=source_units)
    if type(fem_file) is str or issubclass(type(fem_file), pathlib.Path):
        a.read_fem(fem_file, fem_format, name, fem_converter=fem_converter)
    elif type(fem_file) is list:
        for i, f in enumerate(fem_file):
            fem_format_in = fem_format if fem_format is None else fem_format[i]
            name_in = name if name is None else name[i]
            a.read_fem(f, fem_format_in, name_in, fem_converter=fem_converter)
    else:
        raise ValueError(f'fem_file must be either string or list. Passed type was "{type(fem_file)}"')

    return a


__all__ = [
    "Assembly",
    "Part",
    "FEM",
    "from_ifc",
    "from_fem",
    "Beam",
    "Plate",
    "Pipe",
    "PipeSegStraight",
    "PipeSegElbow",
    "Wall",
    "Penetration",
    "Section",
    "Material",
    "Shape",
    "Node",
    "Placement",
    "PrimBox",
    "PrimCyl",
    "PrimExtrude",
    "PrimRevolve",
    "PrimSphere",
    "PrimSweep",
    "CurvePoly",
    "CurveRevolve",
    "LineSegment",
    "ArcSegment",
    "User",
    "Bolts",
    "Weld",
    "fem",
]
