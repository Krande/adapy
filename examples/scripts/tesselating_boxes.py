import os
import random

import trimesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCC.Core.TopoDS import TopoDS_Shape

from ada.occ.tesselating import shape_to_tri_mesh
from ada.visit.colors import color_dict

_col_vals = [(*x, 1) for x in color_dict.values()]


def create_box_grid(x_count: int, y_count: int, z_count: int, min_size: float, max_size: float) -> [TopoDS_Shape]:
    boxes = []

    for x in range(x_count):
        for y in range(y_count):
            for z in range(z_count):
                width = random.uniform(min_size, max_size)
                height = random.uniform(min_size, max_size)
                depth = random.uniform(min_size, max_size)

                box_maker = BRepPrimAPI_MakeBox(
                    gp_Ax2(
                        gp_Pnt(x * max_size, y * max_size, z * max_size),
                        gp_Dir(0, 0, 1),
                    ),
                    width,
                    height,
                    depth,
                )
                box = box_maker.Shape()
                boxes.append(box)

    return boxes


def rand_color() -> tuple[float, float, float, float]:
    return _col_vals[random.randint(0, len(color_dict) - 1)]


def main():
    # Grid dimensions
    x_count, y_count, z_count = 5, 5, 5

    # Randomized box sizes
    min_size, max_size = 0.5, 1.0

    # Create the 3D grid of boxes
    boxes = create_box_grid(x_count, y_count, z_count, min_size, max_size)

    # Tessellate each box in the grid

    tessellated_boxes = [shape_to_tri_mesh(box, rand_color()) for box in boxes]
    scene = trimesh.Scene(geometry=tessellated_boxes)
    os.makedirs("temp", exist_ok=True)
    scene.export("temp/boxes.glb")


if __name__ == "__main__":
    main()
