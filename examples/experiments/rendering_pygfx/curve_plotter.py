import pygfx as gfx
from pygfx.linalg import Vector3
from wgpu.gui.glfw import WgpuCanvas

import ada.visit.render_pygfx_helpers as gfx_utils
from ada import ArcSegment, LineSegment
from ada.core.curve_utils import SegCreator
from ada.geom.points import Point
from ada.occ.tessellating import BatchTessellator
from ada.occ.utils import segments_to_edges
from ada.visit.colors import Color
from ada.visit.render_pygfx import BG_GRAY


def segment_plotter(segments: list[LineSegment | ArcSegment], canvas_title="PyGFX 2D Plotter"):
    scene = gfx.Scene()
    scene.add(gfx.Background(None, gfx.BackgroundMaterial(BG_GRAY.hex)))
    scene_objects = gfx.Group()
    scene.add(scene_objects)

    bt = BatchTessellator()
    mesh_map = {}
    for i, segment in enumerate(segments_to_edges(segments)):
        color = Color.randomize()
        geom_mesh = bt.tessellate_occ_geom(segment, i, color)
        mat = gfx.LineMaterial(thickness=3, color=color.hex)
        mesh = gfx.Line(gfx_utils.geometry_from_mesh(geom_mesh), material=mat)
        scene_objects.add(mesh)
        mesh_map[mesh.id] = segments[i]

    canvas = WgpuCanvas(title=canvas_title, max_fps=60)
    renderer = gfx.renderers.WgpuRenderer(canvas, show_fps=False)

    camera = gfx.OrthographicCamera(110, 110)

    camera.position = Vector3(0, 0, -1)

    controller = gfx.PanZoomController()
    controller.show_object(camera, scene_objects)
    viewport = gfx.Viewport(renderer)
    controller.add_default_event_handlers(viewport, camera)

    def on_click(event: gfx.PointerEvent):
        info = event.pick_info

        if event.button != 1:
            return

        if isinstance(info["world_object"], gfx.Line):
            seg = mesh_map[info["world_object"].id]
            print(f"Clicked on segment: {seg}")
        else:
            print("Clicked on empty space")

    scene_objects.add_event_handler(on_click, "pointer_down")
    display = gfx.Display(canvas=canvas, renderer=renderer, controller=controller, camera=camera)
    display.show(scene)


def main():
    p1 = Point(1, 1)
    p2 = Point(1, 0)
    p3 = Point(0, 0)
    radius = 0.2

    # Testing alternative arc creation
    # v1 = Direction.from_points(p2, p1)
    # v2 = Direction.from_points(p3, p2)
    # arc_1 = create_arc_segment(v1, v2, radius)
    # segments = [LineSegment(), arc_1, LineSegment()]

    sc = SegCreator([p1, [*p2, radius], p3])
    segments = sc.build()

    segment_plotter(segments)


if __name__ == "__main__":
    main()
