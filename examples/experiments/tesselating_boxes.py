import pathlib
import random
import time
import tracemalloc

import trimesh
from OCC.Core.TopoDS import TopoDS_Shape

from ada.occ.geom.solids import make_box
from ada.occ.tessellating import shape_to_tri_mesh
from ada.visit.colors import Color
from ada.visit.gltf.optimize import optimize_glb


def bytes_to_human_readable(size_in_bytes):
    for unit in ["bytes", "KB", "MB", "GB", "TB"]:
        if size_in_bytes < 1024.0:
            break
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} {unit}"


def calculate_time(func):
    # added arguments inside the inner1,
    # if function takes any arguments,
    # can be added like this.
    def inner1(*args, **kwargs):
        # storing time before function execution
        begin = time.time()

        # Start tracing memory usage here
        tracemalloc.start()
        tracemalloc.clear_traces()

        res = func(*args, **kwargs)

        # Stop tracing memory usage here
        # snapshot = tracemalloc.take_snapshot()
        # top_stats = snapshot.statistics("lineno")
        # print("[ Top 10 ]")
        # for stat in top_stats[:10]:
        #     print(stat)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_hr = bytes_to_human_readable(peak)
        current_hr = bytes_to_human_readable(current)
        # storing time after function execution
        end = time.time()

        print(f"[{func.__name__}] | Run Time: {end - begin:.1f}s | Memory: peak={peak_hr}, current={current_hr}")
        return res

    return inner1


@calculate_time
def create_box_grid(x_count: int, y_count: int, z_count: int, min_size: float, max_size: float) -> [TopoDS_Shape]:
    boxes = []

    for x in range(x_count):
        for y in range(y_count):
            for z in range(z_count):
                width = random.uniform(min_size, max_size)
                height = random.uniform(min_size, max_size)
                depth = random.uniform(min_size, max_size)
                box = make_box(x * max_size, y * max_size, z * max_size, width, height, depth)
                boxes.append(box)

    return boxes


@calculate_time
def tessellate_boxes(boxes):
    return [shape_to_tri_mesh(box, Color.randomize()) for box in boxes]


@calculate_time
def create_trimesh_scene(boxes):
    return trimesh.Scene(geometry=boxes)


@calculate_time
def export_trimesh_scene(scene: trimesh.Scene, glb_path: pathlib.Path):
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    scene.export(glb_path)


@calculate_time
def optimize_boxes_glb(glb_path):
    optimize_glb(glb_path)


def main():
    num = 5  # On my machine anything above 10 will push the execution time > 2 seconds
    # Would probably have to write a C++ extension to speed this up
    glb_path = pathlib.Path("temp/boxes.glb")
    print(f"Tessellating {num ** 3} boxes")

    # Grid dimensions
    x_count, y_count, z_count = num, num, num

    # Randomized box sizes
    min_size, max_size = 0.5, 1.0

    # Create the 3D grid of boxes
    boxes = create_box_grid(x_count, y_count, z_count, min_size, max_size)

    # Tessellate each box in the grid
    tessellated_boxes = tessellate_boxes(boxes)
    scene = create_trimesh_scene(tessellated_boxes)
    export_trimesh_scene(scene, glb_path)
    optimize_boxes_glb(glb_path)


if __name__ == "__main__":
    main()
