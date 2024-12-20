import numpy as np
import pygfx as gfx
from PIL import Image
from wgpu.gui.offscreen import WgpuCanvas

import ada


def screenshot(part: ada.Part, filename: str):
    tri_scene = part.to_trimesh_scene()

    canvas = WgpuCanvas(size=(640, 480), pixel_ratio=1)
    renderer = gfx.renderers.WgpuRenderer(canvas)

    scene = gfx.Scene()
    geom = scene.add(gfx.Group())
    meshes = []
    for mesh in tri_scene.geometry.values():
        meshes.append(
            gfx.Mesh(
                gfx.geometry_from_trimesh(mesh),
                gfx.MeshPhongMaterial(),
            )
        )
    geom.add(*meshes)
    dir_light = gfx.DirectionalLight()
    scene.add(gfx.AmbientLight(intensity=0.5))
    mesh0 = meshes[0]
    camera = gfx.PerspectiveCamera(70, 16 / 9)
    camera.show_object(mesh0, view_dir=(-1, -1, -1))
    scene.add(camera)
    scene.add(dir_light)
    canvas.request_draw(lambda: renderer.render(scene, camera))
    im1 = canvas.draw()
    image = Image.fromarray(np.asarray(im1))
    # Save the image to a file
    image.save(filename)
