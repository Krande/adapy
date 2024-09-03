from __future__ import annotations

import ada
from ada.base.types import GeomRepr


def build_box_model(geom_repr: str | GeomRepr, use_hex_quad):
    # Build Model
    if isinstance(geom_repr, str):
        geom_repr = GeomRepr.from_str(geom_repr)

    box = ada.PrimBox("MyBoxShape", (0, 0, 0), (1, 1, 1))
    a = ada.Assembly() / (ada.Part("MyBoxPart") / [box])

    # Create FEM mesh
    p = a.get_part("MyBoxPart")
    if geom_repr == geom_repr.SOLID:
        props = dict(use_hex=use_hex_quad)
        surf_props = dict()
    else:  # geom_repr is ada.fem.Elem.EL_TYPES.SHELL:
        props = dict(use_quads=use_hex_quad)
        surf_props = dict(surf_positive=True)

    p.fem = p.to_fem_obj(0.5, shp_repr=geom_repr, interactive=False, **props)

    # Add Step
    step = a.fem.add_step(ada.fem.StepImplicitStatic("MyStep"))

    # Add Boundary condition
    btn_nodes = box.bbox().sides.bottom(return_fem_nodes=True)
    p.fem.add_bc(ada.fem.Bc("fix", ada.fem.FemSet("BottomNodes", btn_nodes), [1, 2, 3]))

    # Add surface load

    surface = p.fem.add_surface(box.bbox().sides.front(return_surface=True, surface_name="FrontSurface", **surf_props))
    step.add_load(ada.fem.LoadPressure("PressureFront", 200, surface))
    return a


def test_surface_box_solid_tet(tmp_path):
    a = build_box_model("solid", False)
    surface = a.parts["MyBoxPart"].fem.surfaces["FrontSurface"]
    assert len(surface.fem_set) == 8

    # a.to_fem("MyFemBox_so_tet", "abaqus", overwrite=True, scratch_dir=tmp_path)


def test_surface_box_solid_hex(tmp_path):
    a = build_box_model("solid", True)
    surface = a.parts["MyBoxPart"].fem.surfaces["FrontSurface"]

    assert len(surface.fem_set) == 4

    # a.to_fem("MyFemBox_so_hex", "abaqus", overwrite=True, scratch_dir=tmp_path)


def test_surface_box_shell_tri(tmp_path):
    a = build_box_model("shell", False)
    surface = a.parts["MyBoxPart"].fem.surfaces["FrontSurface"]

    assert len(surface.fem_set.members) == 24

    # a.to_fem("MyFemBox_sh_tri", "abaqus", overwrite=True, scratch_dir=tmp_path)


def test_surface_box_shell_quad(tmp_path):
    a = build_box_model("shell", True)
    surface = a.parts["MyBoxPart"].fem.surfaces["FrontSurface"]

    assert len(surface.fem_set.members) == 16

    # a.to_fem("MyFemBox_sh_quad", "abaqus", overwrite=True, scratch_dir=tmp_path)


def test_surface_beam(tmp_path):
    from ada.fem.meshing import GmshOptions

    # Build Model
    bm = ada.Beam("MyBeam", (0, 0, 0), (0, 0, 1), "BG200x150x6x6")
    p = ada.Part("MyBmPart") / [bm]
    a = ada.Assembly() / p

    # Create FEM mesh
    p.fem = p.to_fem_obj(0.10, "solid", interactive=False, options=GmshOptions(Mesh_ElementOrder=2))

    # Add Step
    step = a.fem.add_step(ada.fem.StepImplicitStatic("MyStep"))

    # Add Boundary Condition
    start_of_beam = bm.bbox().sides.back(return_fem_nodes=True)
    p.fem.add_bc(ada.fem.Bc("fix", ada.fem.FemSet("bc_fix", start_of_beam), [1, 2, 3]))

    # Add Surface Load
    surface_top = p.fem.add_surface(bm.bbox().sides.top(return_surface=True, surf_name="TopSurface"))
    step.add_load(ada.fem.LoadPressure("PressureTop", 1e6, surface_top))

    # a.to_fem("MyFemBeam_100mm_2nd_order", "abaqus", overwrite=True, execute=False, scratch_dir=tmp_path)
