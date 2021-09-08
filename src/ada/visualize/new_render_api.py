from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Tuple, Union

from ada.concepts.piping import Pipe
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall
from ada.fem.shapes import ElemType

from .occ_to_threejs import VizObj


class Renderer(Enum):
    VTK = auto()
    PYTHREEJS = auto()


@dataclass
class Camera:
    target: Tuple[float, float, float] = (0, 0, 0)
    origin: Tuple[float, float, float] = (3, 3, 0)
    up: Tuple[float, float, float] = (0, 0, 1)
    fov: float = 55.0


@dataclass
class Visualize:
    renderer = Renderer.PYTHREEJS
    camera: Camera = Camera()
    objects: List[VizObj] = None
    edge_color = (32, 32, 32)

    def __post_init__(self):
        if self.objects is None:
            self.objects = []

    def add_obj(self, obj: Union[Beam, Plate, Wall, Shape, Pipe], geom_repr: str = ElemType.SOLID):
        self.objects.append(VizObj(obj, geom_repr, self.edge_color))

    def set_camera(self, origin, target, fov):
        self.camera.origin = origin
        self.camera.target = target
        self.camera.fov = fov

    def display(self):
        from ipygany import Scene

        meshes = []
        for obj in self.objects:
            meshes.append(obj.convert_to_ipygany_mesh())
        return Scene(meshes)
