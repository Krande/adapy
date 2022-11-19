import ada
from ada.fem.meshing import GmshOptions, GmshSession


def test_hex_element():
    box = ada.PrimBox("box", (0, 0, 0), (1, 1, 1))
    fem = box.to_fem_obj(10, "solid", use_hex=True)
    assert len(fem.elements.elements) == 1
    mesh = fem.to_mesh()
    coords = mesh.nodes.coords
    edges, faces = mesh.get_edges_and_faces_from_mesh()
    assert faces.shape == (12, 3)
    assert edges.shape == (13, 2)
    assert coords.shape == (8, 3)

    # ada.Assembly("Assembly") / (ada.Part("BoxP", fem=fem) / box).to_gltf("temp/Box.glb")


def test_hex_meshed_plate(test_meshing_dir):
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)

    with GmshSession(silent=True) as gs:
        gs.add_obj(pl, "solid")
        gs.mesh(0.1, use_hex=True)
        # gs.open_gui()
        fem = gs.get_fem()

    fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON = "C3D8"

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["HEXAHEDRON"]) == 100

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / pl)).to_fem(
    #     "HexMesh_plate_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_hex_meshed_beam(test_meshing_dir):
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE400")

    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, "solid")
        gs.mesh(0.1, use_hex=True)
        fem = gs.get_fem()

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / bm)).to_fem(
    #     "HexMesh_beam_ca", "code_aster", overwrite=True, scratch_dir=test_meshing_dir
    # )

    assert len(el_types.keys()) == 1
    assert len(el_types["HEXAHEDRON"]) == 140


def test_hex20_meshed_beam(test_meshing_dir):
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE400")

    with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
        gs.add_obj(bm, "solid")
        gs.mesh(0.1, use_hex=True)
        fem = gs.get_fem()

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / bm)).to_fem(
    #     "HexMesh_beam_ca", "code_aster", overwrite=True, scratch_dir=test_meshing_dir
    # )

    assert len(el_types.keys()) == 1
    assert len(el_types["HEXAHEDRON20"]) == 140


# def test_hex_meshed_plate_with_hole(test_meshing_dir):
#     pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
#     pl.add_penetration(ada.PrimCyl("Mycyl", (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), 0.2))
#
#     with GmshSession(options=GmshOptions(Mesh_ElementOrder=1), silent=True) as gs:
#         gs.add_obj(pl, "solid")
#         gs.mesh(0.1, use_hex=True)
#         fem = gs.get_fem()
#
#     el_types = {el_type: list(group) for el_type, group in fem.elements.group_by_type()}
#
#     assert len(el_types.keys()) == 1
#     assert len(el_types["QUAD"]) == 117
#
#     # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / pl)).to_fem(
#     #     "QuadMesh_w_pen_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir
#     # )
