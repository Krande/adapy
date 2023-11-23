import ada
from ada.visit.render_pygfx import RendererPyGFX


def test_read_fem_object():
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    p = ada.Part("part") / bm
    p.fem = p.to_fem_obj(0.1, "line")
    a = ada.Assembly() / p
    a.to_gltf("beam_wMesh.glb")

    renderer = RendererPyGFX(no_gui=True)
    renderer.add_trimesh_scene(a.to_trimesh_scene(), "myFEM")


def test_visualize_box_geom():
    bm = ada.BeamTapered("bm", (0, 0, 0), (1, 0, 0), "BG300x200x8x10", "BG200x200x8x10")
    a = ada.Assembly() / (ada.Part("part") / bm)

    renderer = RendererPyGFX(no_gui=True)
    renderer.add_trimesh_scene(a.to_trimesh_scene(), "myFEM")
