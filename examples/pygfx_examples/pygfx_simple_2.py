import numpy as np

# from rendercanvas.auto import RenderCanvas, loop
from rendercanvas.pyside6 import RenderCanvas, loop
import pygfx as gfx


canvas = RenderCanvas()
renderer = gfx.renderers.WgpuRenderer(canvas)
scene = gfx.Scene()

xx = np.linspace(-50, 50, 10)
yy = np.random.uniform(20, 50, 10)
geometry = gfx.Geometry(positions=[(x, y, 0) for x, y in zip(xx, yy, strict=True)])
ob = gfx.Points(geometry, gfx.PointsMaterial(color=(0, 1, 1, 1), size=20, pick_write=True))
scene.add(ob)

camera = gfx.OrthographicCamera(120, 120)


@ob.add_event_handler("pointer_down")
def offset_point(event):
    print(event)
    info = event.pick_info
    if "vertex_index" in info:
        i = round(info["vertex_index"])
        geometry.positions.data[i, 1] *= -1
        geometry.positions.update_range(i)
        canvas.request_draw()


if __name__ == "__main__":
    canvas.request_draw(lambda: renderer.render(scene, camera))
    loop.run()
