from dataclasses import dataclass

from ada.geom.points import Point


@dataclass
class Direction:
    x: float
    y: float
    z: float


@dataclass
class Axis2Placement3D:
    location: Point
    axis: Direction
    ref_direction: Direction
