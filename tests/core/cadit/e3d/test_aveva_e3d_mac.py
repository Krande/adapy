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
        (80.418, 289.143, 518.352),  # end coords in metres
        "HEA300",
        "S355",
    )

    plate = ada.Plate.from_3d_points(
        "P1",
        points=(  # 4 (or more) corners in metres (E,N,U)
            (0.005, 0.000, 0.000),
            (0.005, 0.070, 0.000),
            (0.058, 0.070, 0.000),
            (0.058, 0.000, 0.000),
        ),
        t=0.01,
        mat="S420",
    )

    subframe / beam  # attach AFTER Parts exist
    subframe / plate

    # --- 5  write macro ---------------------------------------------------------
    string_obj = StringIO()
    asm.to_aveva_mac(
        string_obj,
        spec_map={"HEA300": "/TEST_TT-A-SPEC/S355G11HEA300"},
        panel_spec_map={"PL10": "/TEST_TT/VLE99PL010"},
        material_map={"S355": "/S355NH_TTT_tt", "S420": "/S420NH_TTT_tt"},
        panel_material_map={"S355": "/VLE99_TTT_tt", "S420": "/VLE99_TTT_tt"},
    )
    string_obj.seek(0)
    pml = string_obj.read()
    print(pml)
