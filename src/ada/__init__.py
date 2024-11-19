from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from ada import fem
from ada.api.beams import Beam, BeamRevolve, BeamSweep, BeamTapered
from ada.api.curves import ArcSegment, CurvePoly2d, CurveRevolve, LineSegment
from ada.api.fasteners import Bolts, Weld
from ada.api.groups import Group
from ada.api.nodes import Node
from ada.api.piping import Pipe, PipeSegElbow, PipeSegStraight
from ada.api.plates import Plate, PlateCurved
from ada.api.primitive_boolean import Boolean
from ada.api.primitives import (
    PrimBox,
    PrimCone,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.api.spatial import Assembly, Part
from ada.api.transforms import Instance, Placement, Transform
from ada.api.user import User
from ada.api.walls import Wall
from ada.base.units import Units
from ada.config import configure_logger
from ada.core.utils import Counter
from ada.fem import FEM
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.materials import Material
from ada.sections import Section
from ada.visit.config import set_jupyter_part_renderer
from ada.warnings import deprecated

if TYPE_CHECKING:
    import ifcopenshell

    from ada.fem.formats.sesam.results.read_cc import CCData
    from ada.fem.results.common import FEAResult

__author__ = "Kristoffer H. Andersen"

# A set of convenience name generators for plates and beams
PL_N = Counter(start=1, prefix="PL")
BM_N = Counter(start=1, prefix="BM")
configure_logger()


def from_ifc(ifc_file: os.PathLike | ifcopenshell.file, units=Units.M, name="Ada") -> Assembly:
    if isinstance(ifc_file, (os.PathLike, str)):
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        print(f'Reading "{ifc_file.name}"')
    else:
        print("Reading IFC file object")

    a = Assembly(units=units, name=name)
    a.read_ifc(ifc_file)
    return a


def from_step(step_file: str | pathlib.Path, source_units=Units.M, **kwargs) -> Assembly:
    a = Assembly()
    a.read_step_file(step_file, source_units=source_units, **kwargs)
    return a


def from_fem(
    fem_file: str | list | pathlib.Path,
    fem_format: str | list = None,
    name: str | list = None,
    enable_cache=False,
    source_units=Units.M,
    fem_converter="default",
) -> Assembly:
    a = Assembly(enable_cache=enable_cache, units=source_units)
    if isinstance(fem_file, str) or issubclass(type(fem_file), pathlib.Path):
        a.read_fem(fem_file, fem_format, name, fem_converter=fem_converter)
    elif isinstance(fem_file, list):
        for i, f in enumerate(fem_file):
            fem_format_in = fem_format if fem_format is None else fem_format[i]
            name_in = name if name is None else name[i]
            a.read_fem(f, fem_format_in, name_in, fem_converter=fem_converter)
    else:
        raise ValueError(f'fem_file must be either string or list. Passed type was "{type(fem_file)}"')

    return a


def from_fem_res(fem_file: str | pathlib.Path, fem_format: str = None) -> FEAResult:
    from ada.fem.formats.postprocess import postprocess

    return postprocess(fem_file, fem_format)


def from_sesam_cc(fem_file: str | pathlib.Path) -> dict[str, CCData]:
    from ada.fem.formats.sesam.results.read_cc import read_cc_file

    return read_cc_file(fem_file)


def from_genie_xml(xml_path, **kwargs) -> Assembly:
    from ada.cadit.gxml.store import GxmlStore

    gxml = GxmlStore(xml_path)
    p = gxml.to_part(**kwargs)
    return Assembly(name=kwargs.get("name", p.name)) / p


__all__ = [
    "Assembly",
    "Part",
    "FEM",
    "from_ifc",
    "from_fem",
    "Beam",
    "BeamTapered",
    "BeamSweep",
    "BeamRevolve",
    "Boolean",
    "deprecated",
    "Group",
    "Plate",
    "PlateCurved",
    "Pipe",
    "PipeSegStraight",
    "PipeSegElbow",
    "Wall",
    "Section",
    "Material",
    "Shape",
    "Node",
    "Point",
    "Direction",
    "Placement",
    "PrimBox",
    "PrimCone",
    "PrimCyl",
    "PrimExtrude",
    "PrimRevolve",
    "PrimSphere",
    "PrimSweep",
    "CurvePoly2d",
    "CurveRevolve",
    "LineSegment",
    "ArcSegment",
    "Transform",
    "Instance",
    "User",
    "Bolts",
    "Weld",
    "Units",
    "fem",
    "set_jupyter_part_renderer",
    "BM_N",
    "PL_N",
]
