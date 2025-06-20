from io import StringIO

import ada


def test_aveva_mac_export():
    """Build a full SITE-->ZONE-->STRU-->FRMW-->SBFR hierarchy and export PML."""

    # --- 1  create assembly -----------------------------------------------------
    asm = ada.Assembly("ADA")  # depth 1 --> SITE

    # --- 2  create parts --------------------------------------------------------
    zone = ada.Part("Zone_A")  # depth 2 --> ZONE
    stru = ada.Part("Stru_A")  # depth 3 --> STRU
    frame = ada.Part("Frame_A")  # depth 4 --> FRMW
    subframe = ada.Part("SubFrame_A")  # depth 5 --> SBFR
    # (If you want a second SBFR level, just nest another Part here.)

    # --- 3  stitch the hierarchy BEFORE adding geometry -------------------------
    asm / zone  # attach to assembly
    zone / stru
    stru / frame
    frame / subframe  # deepest Part for this demo

    # --- 4  create geometry -----------------------------------------------------
    beam = ada.Beam(
        "beam1",
        (79.925, 289.143, 518.352),  # start coords in metres
        (80.925, 289.143, 518.352),  # end coords in metres
        "HEA300",
        "S355",
    )
    origin = ada.Point(79.925, 289.143, 518.352)
    plate = ada.Plate("Pl1", points=((0, 0), (1, 0), (1, 1), (0, 1)), t=0.01, mat="S420", origin=origin)
    plate2 = ada.Plate(
        "Pl2",
        points=((0, 0), (1, 0), (1, 1), (0, 1)),
        t=0.01,
        mat="S420",
        origin=origin,
        normal=(1, 0, 0),
        xdir=(0, 1, 0),
    )
    plate3 = ada.Plate(
        "Pl3",
        points=((0, 0), (1, 0), (1, 1), (0, 1)),
        t=0.01,
        mat="S420",
        origin=origin,
        normal=(0.7, 0.7, 0),
        xdir=(0, 0, 1),
    )
    p1 = plate3.poly.points3d[0]

    p2 = p1 + ada.Direction(1, 0, 0) * plate3.poly.ydir
    bm2 = ada.Beam(
        "beam2",
        plate3.poly.points3d[0],  # start coords in metres
        p2,  # end coords in metres
        "HEA300",
        "S355",
        up=plate3.poly.normal,
    )
    p2 = p1 + ada.Direction(0, 0, 1)
    bm3 = ada.Beam(
        "beam3",
        plate3.poly.points3d[0],  # start coords in metres
        p2,  # end coords in metres
        "HEA300",
        "S355",
        up=plate3.poly.normal,
    )
    subframe / (beam, bm2, bm3)  # attach AFTER Parts exist
    subframe / (plate, plate2, plate3)  # attach multiple objects at once

    # --- 5  write macro ---------------------------------------------------------
    string_obj = StringIO()
    asm.to_aveva_mac(
        string_obj,
        beam_spec_map={"HEA300": "/TEST_TT-A-SPEC/S355G11HEA300"},
        panel_spec_map={"PL10": "/TEST_TT/VLE99PL010"},
        beam_material_map={"S355": "/S355NH_TTT_tt", "S420": "/S420NH_TTT_tt"},
        panel_material_map={"S355": "/VLE99_TTT_tt", "S420": "/VLE99_TTT_tt"},
    )
    string_obj.seek(0)
    pml = string_obj.read()
    print(pml)
