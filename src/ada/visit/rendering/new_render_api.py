from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Tuple, Union

from ada import FEM, Assembly, Beam, Part, Pipe, Plate, Shape, Wall
from ada.fem.shapes import ElemType


class Renderer(Enum):
    VTK = auto()
    PYTHREEJS = auto()
    IPYGANY = auto()
    PYVISTA = auto()


@dataclass
class Camera:
    target: Tuple[float, float, float] = (0, 0, 0)
    origin: Tuple[float, float, float] = (3, 3, 0)
    up: Tuple[float, float, float] = (0, 0, 1)
    fov: float = 55.0


@dataclass
class Visualize:
    parent: Union[Assembly, Part, Beam, Plate, Wall, Shape, Pipe, FEM] = None
    renderer: Renderer = Renderer.PYVISTA
    camera: Camera = field(default_factory=Camera)
    objects: List[VizObj] = None
    edge_color = (32, 32, 32)

    def __post_init__(self):
        if self.objects is None:
            self.objects = []

    def add_obj(self, obj: Union[Assembly, Part, Beam, Plate, Wall, Shape, Pipe, FEM], geom_repr: str = ElemType.SOLID):
        if issubclass(type(obj), Part):
            for sub_obj in obj.get_all_physical_objects():
                geom_repr_ = sub_obj.metadata.get("geom_repr", geom_repr)
                self.objects.append(VizObj(sub_obj, geom_repr_, self.edge_color))
        elif type(obj) is FEM:
            self.objects.append(VizObj(obj, geom_repr, self.edge_color))
        else:
            geom_repr = obj.metadata.get("geom_repr", geom_repr)
            self.objects.append(VizObj(obj, geom_repr, self.edge_color))

    def set_camera(self, origin, target, fov):
        self.camera.origin = origin
        self.camera.target = target
        self.camera.fov = fov

    def display(self, off_screen_file=None, **kwargs):
        from .renderer_ipygany import render_ipyany_scene
        from .renderer_pyvista import render_ipyvista_scene

        render_map = {Renderer.PYVISTA: render_ipyvista_scene, Renderer.IPYGANY: render_ipyany_scene}
        renderer = render_map[self.renderer]

        if len(self.objects) == 0 and self.parent is not None:
            self.add_obj(self.parent)

        return renderer(self, off_screen_file=off_screen_file, **kwargs)


@dataclass
class VizObj:
    obj: Union[Beam, Plate, Wall, Shape, Pipe, FEM]
    geom_repr: str = ElemType.SOLID
    edge_color: tuple = None

    def get_geom(self, geom_repr):
        if geom_repr == ElemType.SOLID:
            return self.obj.solid()
        elif geom_repr == ElemType.SHELL:
            return self.obj.shell()
        elif geom_repr == ElemType.LINE:
            return self.obj.line()
        else:
            raise ValueError(f'Unrecognized "{geom_repr}".')

    def obj_to_verts_and_faces(self, parallel=True, render_edges=True, quality=1.0):
        from .renderer_occ import occ_shape_to_faces

        geom = self.get_geom(self.geom_repr)
        np_vertices, np_faces, np_normals, edges = occ_shape_to_faces(geom, quality, render_edges, parallel)
        return np_vertices, np_faces, np_normals, edges

    def convert_to_pythreejs_mesh(self):
        from .renderer_pythreejs import OccToThreejs

        o = OccToThreejs()
        mesh, edges = o.occ_shape_to_threejs(
            self.obj.solid(), self.obj.colour, self.edge_color, self.obj.transparent, self.obj.opacity
        )
        return mesh

    def convert_to_ipygany_mesh(self):
        from .renderer_ipygany import mesh_from_arrays

        np_vertices, np_faces, np_normals, edges = self.obj_to_verts_and_faces()
        return mesh_from_arrays(np_vertices, np_faces)

    def convert_to_pyvista_mesh(self):
        from .renderer_pyvista import mesh_from_arrays

        np_vertices, np_faces, np_normals, edges = self.obj_to_verts_and_faces()
        return mesh_from_arrays(np_vertices, np_faces)
