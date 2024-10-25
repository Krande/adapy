import ada
from ada.config import Config, logger
from ada.fem.meshing import GmshOptions


def main():
    # Place the following code wherever in the code to break
    # br_names = Config().meshing_open_viewer_breakpoint_names
    # if br_names is not None and "partition_isect_pl_after_fragment" in br_names:
    #    gmsh_session.open_gui()

    Config().update_config_globally(
        "meshing_open_viewer_breakpoint_names",
        [
            "partition_isect_pl_after_fragment"
        ],
    )

    p1x1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1_5 = ada.Plate("pl1_5", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.5)))
    bm_1 = ada.Beam("bm1", (0, 0, 0.5), (1, 0, 0.5), "IPE180", up=[0, 0, 1])
    bm_2 = ada.Beam("bm2", (0.5, 0, 0.5), (0.5, 1, 0.5), "HP180x8")
    pl3 = ada.Plate("pl3", p1x1, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))

    p = ada.Part("MyFem") / [pl1_5, pl3, bm_1, bm_2]
    p.show()

    fem = p.to_fem_obj(0.3, bm_repr="line", pl_repr="shell", options=GmshOptions(Mesh_Algorithm=6))
    fem.show(solid_beams=True)
    p.fem = fem

    a = ada.Assembly("Test") / p
    # a.to_fem("ADA_pl_mesh", "usfos", overwrite=True)

    assert len(fem.nodes) == 10
    assert len(fem.elements) == 13


if __name__ == "__main__":
    logger.setLevel("INFO")
    main()
