from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Assembly, Shape
    from ada.geom.solids import Box


@dataclass
class Space:
    name: str
    geometry: Box = None


@dataclass
class SpaceGraphModel:
    spaces: list[Space] = None


def shape_to_space(shape: Shape) -> Space:
    return Space(shape.name, shape.geom.geometry)


def space_graph_from_assembly(a: Assembly) -> SpaceGraphModel:
    from ada import Shape
    import ada.occ.geom.solids as geo_so

    print("extracting shape graph from assembly")

    spaces = []
    space_map = {}
    for shape in a.get_all_physical_objects(Shape):
        space = Space(shape.name, shape.geom.geometry)
        occ_geom = geo_so.make_box_from_geom(shape.geom.geometry)

        spaces.append(space)

    return SpaceGraphModel(spaces=spaces)
