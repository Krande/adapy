# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#

import ada
from ada.base.types import GeomRepr
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.sections.categories import BaseTypes
from ada.visit.colors import Color
from ada.visit.render_backend import MeshInfo, SqLiteBackend
from ada.visit.render_pygfx import RendererPyGFX

# from ada.config import logger
#
# logger.setLevel("INFO")


def main():
    objects = []
    render_override = {}
    bm1 = ada.Beam("my_beam_x", (2, 0, 0), (2, 0, 1), "IPE300", color="red")
    bm1.add_boolean(ada.PrimCyl("cyl2", (1.8, 0, 0.5), (2.2, 0, 0.5), 0.1))

    bm2 = ada.Beam("my_beam_y", (0, 2, 0), (0, 2, 1), "IPE300", color="green")
    bm3 = ada.Beam("my_beam_z", (0, 0, 2), (0, 0, 3), "IPE300", color="blue")
    bm4 = ada.Beam("my_beam_shell", (1, 1, 0), (1, 1, 1), "IPE300", color="yellow")
    bm5 = ada.Beam("my_beam_xyz_shell", (3, 2, 1), (3.5, 2.5, 1.5), "IPE300", color="red")
    bm6 = ada.Beam("my_beam_xyz", (1, 2, 1), (1.5, 2.5, 1.5), "IPE300", color="yellow")
    bm7_taper = ada.BeamTapered("my_Ibeam_taper", (2, 2, 1), (2.5, 2.5, 1.5), "IPE600", "IPE300", color="blue")
    bm8_taper = ada.BeamTapered(
        "my_BOX_beam_taper", (4, 2, 1), (4.5, 2.5, 1.5), "BG300x200x8x10", "BG200x200x8x10", color="red"
    )
    render_override.update({bm4.guid: GeomRepr.SHELL, bm5.guid: GeomRepr.SHELL})

    # All beam profiles as solids
    origin = Point(0.5, 0, 2)
    spacing = Direction(1.0, 0, 0)
    for i, in_str in enumerate(BaseTypes.get_valid_example_map().values()):
        p1 = origin + i * spacing
        p2 = p1 + Direction(0, 1, 0)
        _bm = ada.Beam(f"bm_{8 + i}_{in_str}", p1, p2, in_str, color=Color.randomize())
        objects.append(_bm)

    origin = Point(0.5, 0, 3)
    for i, in_str in enumerate(BaseTypes.get_valid_example_map().values()):
        p1 = origin + i * spacing
        p2 = p1 + Direction(0, 1, 0)
        _bm = ada.Beam(f"bm_{44 + i}_{in_str}", p1, p2, in_str, color=Color.randomize())
        objects.append(_bm)
        render_override[_bm.guid] = GeomRepr.SHELL

    pl2_points = [(0, 0, 0.2), (0, 1, 0.2), (1, 1, 0.2), (1, 0, 0.2)]
    pl1 = ada.Plate("pl1", [(0, 0), (0, 1), (1, 1), (1, 0)], 0.01, origin=(0, 0, 4), color="red")
    pl2 = ada.Plate("pl2", pl2_points, 0.01, origin=(2, 0, 4.5), color="blue")
    pl3 = ada.Plate(
        "pl3",
        [(0, 0, 0.2), (0, 1, 0.2), (1, 1), (1, 0)],
        0.01,
        origin=(4, 0, 4),
        n=(0, -1, 0),
        xdir=(1, 0, 0),
        color="red",
    )
    pl4 = ada.Plate(
        "pl4", [(0, 0), (0, 1), (1, 1), (1, 0)], 0.01, origin=(4, 1, 4), n=(0, -1, 0), xdir=(1, 0, 0), color="blue"
    )
    render_override[pl3.guid] = GeomRepr.SHELL
    ori = pl2.poly.orientation
    for a in (30, 60, 90, 120, 150, 180):
        new_ori = ori.rotate([1, 0, 0], a)
        pl_rot_x = ada.Plate(f"pl_rot{a}", pl2_points, 0.01, origin=pl2.poly.origin, orientation=new_ori, color="blue")
        objects.append(pl_rot_x)

    box1 = ada.PrimBox("box1", (1, 0, 0), (1.5, 0.5, 0.5), color="red")
    box1.add_boolean(ada.PrimBox("box2", (1.25, -0.25, 0.25), (1.75, 0.25, 0.75)))

    rev = ada.PrimRevolve("rev1", pl2_points, 78, (3, 2, 3), (1, 0, 0), (0, -1, 0), color="green")

    cyl1 = ada.PrimCyl("cyl1", (3, 0, 0), (3, 0.5, 0.5), 0.3, color="green")
    cone1 = ada.PrimCone("cone1", (4, 0, 0), (4, 0.5, 0.5), 0.3, color="green")

    sphere1 = ada.PrimSphere("sphere1", (5, 0, 0), 0.3, color="green")
    sphere1.add_boolean(ada.PrimSphere("sphere2", (5.5, 0, 0), 0.3), "union")
    sphere1.add_boolean(ada.PrimSphere("sphere3", (5.25, 0, 0), 0.2))

    po = [Point(1, 1, 3) + x for x in [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)]]
    pipe1 = ada.Pipe("pipe1", po, "PIPE200x5", color="green")

    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.05),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (0.2, 0), (0.2, 0.2), (0, 0.2)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red")

    objects += [
        bm1,
        bm2,
        bm3,
        box1,
        bm4,
        bm5,
        bm6,
        cyl1,
        cone1,
        sphere1,
        rev,
        bm7_taper,
        bm8_taper,
        pl1,
        pl2,
        pl3,
        pl4,
        pipe1,
        sweep,
    ]

    a = ada.Assembly() / (ada.Part("MyBeam") / objects)
    # a.to_stp("temp/part.stp", geom_repr_override=render_override)
    # a.to_ifc("temp/part.ifc", geom_repr_override=render_override)
    a.to_viewer()
    # render = RendererPyGFX(render_backend=SqLiteBackend("temp/meshes.db"))
    #
    # def _on_click(event, mesh_data: MeshInfo):
    #     obj = a.get_by_guid(mesh_data.mesh_id)
    #     print(obj)
    #
    # render.on_click_post = _on_click
    # render.add_part(a, render_override=render_override)
    # render.show()


if __name__ == "__main__":
    main()
