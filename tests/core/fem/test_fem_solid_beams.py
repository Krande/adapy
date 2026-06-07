"""FEM line/beam elements rendered as solid (swept-profile) geometry, mirroring the FEA-results
viewer and fem.show(solid_beams=True). Without it, beam elements render as bare centerlines."""

import ada
from ada.fem.meshing import GmshSession
from ada.visit.render_params import FEARenderParams, RenderParams


def _beam_line_fem():
    bm = ada.Beam("b", (0, 0, 0), (5, 0, 0), "IPE300")
    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, "line")
        gs.mesh(1.0)
        fem = gs.get_fem()
    return fem


def test_fem_to_mesh_carries_section_tables():
    # to_mesh must expose section/material/vector tables + elem_data so the solid-beam path
    # can resolve each line element's profile (Section/Material ids are unset in memory).
    fem = _beam_line_fem()
    mesh = fem.to_mesh()
    assert mesh.elem_data is not None and len(mesh.elem_data) == sum(1 for _ in fem.elements.lines)
    assert mesh.sections and mesh.materials


def _scene_tris(fem, solid_beams):
    p = ada.Part("p")
    p.fem = fem
    a = ada.Assembly("a") / p
    params = RenderParams(
        merge_meshes=True,
        stream_from_ifc_store=False,
        fea_params=FEARenderParams(solid_beams=solid_beams),
    )
    scene = a.to_trimesh_scene(params=params)
    return sum(int(g.faces.shape[0]) for g in scene.geometry.values() if getattr(g, "faces", None) is not None)


def test_solid_beams_produce_triangles():
    # solid beams turn the beam line elements into swept-profile solids -> real triangles,
    # where the line render produces none.
    assert _scene_tris(_beam_line_fem(), solid_beams=False) == 0
    assert _scene_tris(_beam_line_fem(), solid_beams=True) > 0
