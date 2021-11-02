import ada
from ada.fem.meshing import GmshSession


def test_hex_meshed_plate(test_meshing_dir):
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)

    with GmshSession(silent=True) as gs:
        gs.add_obj(pl, "solid")
        gs.mesh(0.1, use_hex=True)
        fem = gs.get_fem()

    fem.options.ABAQUS.default_elements.SOLID.HEXAHEDRON = "C3D8"

    (ada.Assembly() / (ada.Part("MyPart", fem=fem) / pl)).to_fem(
        "HexMesh_plate_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    )


def test_hex_meshed_beam(test_meshing_dir):
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE400")

    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, "solid")
        gs.mesh(0.1, use_hex=True)
        fem = gs.get_fem()

    print(fem)
    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / bm)).to_fem(
    #     "HexMesh_beam_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )
