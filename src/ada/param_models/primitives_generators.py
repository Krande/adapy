import random
from dataclasses import dataclass
from typing import Iterable

from ada.geom import Geometry
from ada.geom.solids import Box
from ada.visit.colors import random_color
from ada.visit.gltf.graph import GraphStore, GraphNode


@dataclass
class BoxGenerator:
    grid_size: int = 2
    min_size: float = 0.5
    max_size: float = 1.0
    graph: GraphStore = None

    def generate_box_grid(self) -> Iterable[Geometry]:

        box_id = 0

        root = GraphNode('root', 0)
        graph = {0: root}

        for x in range(self.grid_size):
            for y in range(self.grid_size):
                for z in range(self.grid_size):
                    width = random.uniform(self.min_size, self.max_size)
                    height = random.uniform(self.min_size, self.max_size)
                    depth = random.uniform(self.min_size, self.max_size)

                    box = Box.from_xyz_and_dims(x, y, z, width, height, depth)
                    graph[box_id] = GraphNode(f"n{box_id}", box_id, parent=root)
                    yield Geometry(box_id, box, random_color())
                    box_id += 1

        self.graph = GraphStore(root, graph)
