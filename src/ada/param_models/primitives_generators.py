from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable

from ada import Beam
from ada.geom import Geometry
from ada.geom.solids import Box
from ada.visit.colors import Color
from ada.visit.gltf.graph import GraphNode, GraphStore


def random_box_geom_at_position(shape_id, x, y, z, min_size, max_size) -> Geometry:
    width = random.uniform(min_size, max_size)
    height = random.uniform(min_size, max_size)
    depth = random.uniform(min_size, max_size)

    box = Box.from_xyz_and_dims(x, y, z, width, height, depth)
    return Geometry(shape_id, box, Color.randomize())


def random_i_beam_at_position(shape_id, x, y, z, min_size, max_size) -> Geometry:
    length = random.uniform(min_size, max_size)
    p1 = [x, y, z]
    p2 = [x, y, z]
    direction = random.randint(0, 2)
    p2[direction] += length
    bm = Beam(f"beam_{shape_id}", p1, p2, "IPE300", color=Color.randomize())
    return bm.solid_geom()


@dataclass
class ShapeGenerator:
    grid_size: int = 2
    min_size: float = 0.5
    max_size: float = 0.85
    graph: GraphStore = None
    shape_function: Callable[[int, float, float, float, float, float], Geometry] = random_box_geom_at_position

    def generate_shape_grid(self) -> Iterable[Geometry]:
        shape_id = 0

        root = GraphNode("root", 0)
        graph = {0: root}

        for x in range(self.grid_size):
            for y in range(self.grid_size):
                for z in range(self.grid_size):
                    node = GraphNode(f"n{shape_id}", shape_id, parent=root)
                    geom = self.shape_function(shape_id, x, y, z, self.min_size, self.max_size)
                    geom.id = shape_id
                    graph[shape_id] = node
                    root.children.append(node)
                    yield geom
                    shape_id += 1

        self.graph = GraphStore(root, graph)
