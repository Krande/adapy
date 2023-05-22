import random
from itertools import groupby
from typing import Iterable

from ada.geom import Geometry
from ada.geom.solids import Box
from ada.occ.tessellating import BatchTessellator
from ada.visit.colors import random_color
from ada.visit.gltf.optimize import concatenate_stores


def random_box_geom(grid_size) -> Iterable[Geometry]:
    min_size = 0.5
    max_size = 1.0
    box_id = 0
    for x in range(grid_size):
        for y in range(grid_size):
            for z in range(grid_size):
                width = random.uniform(min_size, max_size)
                height = random.uniform(min_size, max_size)
                depth = random.uniform(min_size, max_size)

                box = Box.from_xyz_and_dims(x, y, z, width, height, depth)
                yield Geometry(box_id, box, random_color())
                box_id += 1


def test_shape_grid():
    grid_size = 5
    shape_grid = random_box_geom(grid_size)

    bt = BatchTessellator()
    shape_grid_tessellated = bt.batch_tessellate(shape_grid)
    all_shapes = list(shape_grid_tessellated)
    for mat_id, mat in groupby(all_shapes, lambda x: x.material):
        merged_store = concatenate_stores(mat)
        print('ds')

