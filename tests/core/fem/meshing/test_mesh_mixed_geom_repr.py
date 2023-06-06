import pytest

import ada.fem.shapes
from ada import Assembly, Beam, Part, Pipe, Plate, PrimBox, PrimSphere
from ada.api.transforms import Placement
from ada.fem.meshing.concepts import GmshOptions, GmshSession, GmshTask
from ada.fem.meshing.multisession import multisession_gmsh_tasker
from ada.fem.meshing.partitioning.partition_beams import make_ig_cutplanes


@pytest.fixture
def assembly() -> Assembly:
    bm1 = Beam("bm1", (0, 0, 1), (1, 0, 1), "IPE300")
    bm2 = Beam("bm2", (1.1, 0, 1), (2, 0, 1), "IPE300")
    bm3 = Beam("bm3", (2.1, 0, 1), (3, 0, 1), "IPE300")

    placement = Placement(origin=(1, 1, 1), xdir=(1, 0, 0), zdir=(0, 0, 1))
    pl_points = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1 = Plate("pl1", pl_points, 10e-3, placement=placement)

    pipe = Pipe("pipe", [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)], "OD120x6")

    p1, p2 = (1, -2, 0), (2, -1, 1)
    shp1 = PrimBox("MyBox", p1, p2)
    shp1.add_boolean(PrimSphere("MyCutout", p1, 0.5))

    return Assembly() / (Part("MyFemObjects") / [bm1, bm2, bm3, pl1, shp1, pipe])


def test_mix_geom_repr_in_same_session(assembly):
    shape = ada.fem.shapes.ElemShape.TYPES
    bm1 = assembly.get_by_name("bm1")
    bm2 = assembly.get_by_name("bm2")
    bm3 = assembly.get_by_name("bm3")
    pl1 = assembly.get_by_name("pl1")
    pipe = assembly.get_by_name("pipe")
    shp1 = assembly.get_by_name("MyBox")

    p = assembly.get_part("MyFemObjects")

    cut_planes = make_ig_cutplanes(bm2)
    options = GmshOptions(Mesh_ElementOrder=2)

    with GmshSession(silent=True, options=options) as gs:
        gs.add_obj(bm1, "shell")
        solid_bm = gs.add_obj(bm2, "solid")
        gs.add_obj(bm3, "line")
        gs.add_obj(pl1, "shell")
        gs.add_obj(shp1, "solid")
        gs.add_obj(pipe, "shell")

        for cutp in cut_planes:
            gs.add_cutting_plane(cutp, [solid_bm])

        gs.make_cuts()

        gs.mesh(0.1)
        p.fem = gs.get_fem()

    print(p.fem.elements)

    map_assert = {shape.lines.LINE3: 9, shape.solids.TETRA10: 5310, shape.shell.TRI6: 840}

    for key, val in p.fem.elements.group_by_type():
        num_el = len(list(val))
        if key == shape.solids.TETRA10:
            # TODO: Why is the number of elements for different platforms (win, linux and macos)?
            assert map_assert[key] == pytest.approx(num_el, abs=250)
        elif key == shape.shell.TRI6:
            assert map_assert[key] == pytest.approx(num_el, abs=25)
        else:
            assert map_assert[key] == num_el


def test_diff_geom_repr_in_separate_sessions(assembly, test_meshing_dir):
    shape = ada.fem.shapes.ElemShape.TYPES
    bm1 = assembly.get_by_name("bm1")
    bm2 = assembly.get_by_name("bm2")
    p = assembly.get_part("MyFemObjects")

    t1 = GmshTask([bm1], "shell", 0.1, options=GmshOptions(Mesh_ElementOrder=2))
    t2 = GmshTask([bm2], "line", 0.1, options=GmshOptions(Mesh_ElementOrder=1))

    fem = multisession_gmsh_tasker(p.fem, [t1, t2])

    assert len(fem.nodes) == 529
    assert len(fem.elements) == 251

    assert_map = {shape.shell.TRI6: 242, shape.lines.LINE: 9}

    for key, val in p.fem.elements.group_by_type():
        assert assert_map[key] == len(list(val))

    # from ada.fem.steps import StepImplicit
    # a.fem.add_step(StepImplicit("MyStep"))
    # a.to_fem("aba_mixed_order", "abaqus", overwrite=True, scratch_dir=test_meshing_dir)
