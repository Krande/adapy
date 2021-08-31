from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Union

from ada.concepts.piping import Pipe
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall

from .occ_to_threejs import ThreeJSVizObj


class Renderer(Enum):
    VTK = auto()
    PYTHREEJS = auto()


@dataclass
class Camera:
    target: tuple[float, float, float] = (0, 0, 0)
    origin: tuple[float, float, float] = (3, 3, 0)
    up: tuple[float, float, float] = (0, 0, 1)
    fov: float = 55.0


@dataclass
class Visualize:
    renderer = Renderer.VTK
    camera: Camera = Camera()
    objects: List[ThreeJSVizObj] = field(init=False)

    def add_obj(self, obj: Union[Beam, Plate, Wall, Shape, Pipe], edge_color, transparency, opacity):
        self.objects.append(ThreeJSVizObj(obj, edge_color, transparency, opacity))

    def set_camera(self, origin, target, fov):
        self.camera.origin = origin
        self.camera.target = target
        self.camera.fov = fov

    def display(self):
        raise NotImplementedError()
