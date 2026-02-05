from typing import Optional

import numpy as np
import pygfx as gfx
import trimesh
from PIL import Image
from wgpu.gui.offscreen import WgpuCanvas

import ada
from ada.visit.rendering.camera import Camera


def screenshot(part: ada.Part, filename: str, camera: Optional[Camera] = None):
    tri_scene = part.to_trimesh_scene()
    image = trimesh_scene_to_image(tri_scene, camera=camera)
    # Save the image to a file
    image.save(filename)


def trimesh_scene_to_image(tri_scene: trimesh.Scene, camera: Optional[Camera] = None) -> Image.Image:
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

    camera_obj = camera or Camera()

    width, height = canvas.get_logical_size()
    gfx_camera = gfx.PerspectiveCamera(camera_obj.fov, width / height, depth_range=(camera_obj.near, camera_obj.far))

    if camera_obj.fit_view:
        view_dir = (-1, -1, -1)
        if camera_obj.position is not None and camera_obj.look_at is not None:
            view_dir = np.array(camera_obj.look_at) - np.array(camera_obj.position)

        gfx_camera.show_object(geom, view_dir=view_dir, up=camera_obj.up or (0, 0, 1))

        if camera_obj.padding > 0:
            # Adjust zoom for padding. 0.8 zoom means 80% filling.
            gfx_camera.zoom = 1 - camera_obj.padding
    else:
        if camera_obj.up is not None:
            gfx_camera.local.up = camera_obj.up
        if camera_obj.position is not None:
            gfx_camera.local.position = camera_obj.position
        if camera_obj.look_at is not None:
            gfx_camera.look_at(camera_obj.look_at)

    scene.add(gfx_camera)
    scene.add(dir_light)
    canvas.request_draw(lambda: renderer.render(scene, gfx_camera))
    im1 = canvas.draw()
    return Image.fromarray(np.asarray(im1))
