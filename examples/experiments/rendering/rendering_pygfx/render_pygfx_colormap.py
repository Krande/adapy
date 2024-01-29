import numpy as np
import pygfx as gfx
import trimesh

from ada.visit.rendering.render_pygfx import PICKED_COLOR

cube = gfx.Mesh(
    gfx.box_geometry(100, 100, 100),
    gfx.MeshPhongMaterial(color="red", flat_shading=True),
)

faces = [0, 1]
cube_verts = cube.geometry.positions.data
cube_index = cube.geometry.indices.data

# Create a copy of the selected faces from the cube
trim = trimesh.Trimesh(vertices=cube_verts, faces=[cube_index[x] for x in faces])
geom = gfx.Geometry(
    positions=np.ascontiguousarray(trim.vertices, dtype="f4"),
    indices=np.ascontiguousarray(trim.faces, dtype="i4"),
)
selected_mesh = gfx.Mesh(geom, material=gfx.MeshPhongMaterial(color=PICKED_COLOR.hex, flat_shading=True))

# Remove the selected faces from the cube (which is a numpy array located cube.geometry.indices.data)
cube_cut_data = np.delete(cube_index, faces, axis=0)
geom = gfx.Geometry(
    positions=np.ascontiguousarray(cube_verts, dtype="f4"),
    indices=np.ascontiguousarray(cube_cut_data, dtype="i4"),
)
new_cube = gfx.Mesh(
    geom,
    gfx.MeshPhongMaterial(color="red"),
)
group = gfx.Group()
group.add(new_cube)
group.add(selected_mesh)

disp = gfx.Display()
disp.show(group)
