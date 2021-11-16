from ada import Assembly, Beam, Material, Part
from ada.config import Settings
from ada.materials.metals import CarbonSteel

test_dir = Settings.test_dir / "beams"


def test_beam_offset():
    bm1 = Beam(
        "bm1",
        n1=[0, 0, 0],
        n2=[2, 0, 0],
        sec="IPE300",
        mat=Material("SteelMat", CarbonSteel("S420")),
        colour="red",
        up=(0, 0, 1),
        e1=(0, 0, -0.1),
        e2=(0, 0, -0.1),
    )
    bm2 = Beam(
        "bm2",
        n1=[0, 0, 0],
        n2=[2, 0, 0],
        sec="IPE300",
        mat=Material("SteelMat", CarbonSteel("S420")),
        colour="blue",
        up=(0, 0, -1),
        e1=(0, 0, -0.1),
        e2=(0, 0, -0.1),
    )

    a = Assembly("Toplevel") / [Part("MyPart") / [bm1, bm2]]
    a.to_ifc(test_dir / "beams_offset.ifc")


def test_beam_orientation():
    def create_ifc(name, up=(0, 0, 1)):
        a = Assembly("MyAssembly")
        p = Part(name)
        p.add_beam(
            Beam(
                "bm_up",
                n1=[0, 0, 0],
                n2=[2, 0, 0],
                sec="HP200x10",
                mat=Material("SteelMat", CarbonSteel("S420")),
                colour="red",
                up=up,
            )
        )
        a.add_part(p)
        a.to_ifc(test_dir / name)

    create_ifc("hp_cardinal_up")
    create_ifc("hp_cardinal_down", (0, 0, -1))

    aup = Assembly("bm_up")
    aup.read_ifc(test_dir / "hp_cardinal_up.ifc")
    aup.read_ifc(test_dir / "hp_cardinal_down.ifc")


def test_beam_rotation_by_angle():
    # Define beam rotations using vectors
    angles = [0, 90, 180, 270]
    vectorX = [(0, 0, 1), (0, -1, 0), (0, 0, -1), (0, 1, 0)]
    vectorY = [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0)]
    vectorZ = [(0, 1, 0), (-1, 0, 0), (0, -1, 0), (1, 0, 0)]

    a = Assembly("AdaRotatedProfiles")
    p = a.add_part(Part("RotatedBeams"))
    sec = "HP200x10"
    d1 = dict(n1=(0, 0, 0), n2=(5, 0, 0), sec=sec)
    d2 = dict(n1=(0, 0, 0), n2=(0, 5, 0), sec=sec)
    d3 = dict(n1=(0, 0, 0), n2=(0, 0, 5), sec=sec)

    for i, angle in enumerate(angles):
        # Along X-Axis
        bm = p.add_beam(Beam(f"bmX_n{i}_a", **d1, angle=angle))
        assert all([x == y for x, y in zip(bm.up, vectorX[i])]) is True
        p.add_beam(Beam(f"bmX_n{i}_v", **d1, up=vectorX[i]))

        # Along Y-Axis
        bm = p.add_beam(Beam(f"bmY_n{i}_a", **d2, angle=angle))
        assert all([x == y for x, y in zip(bm.up, vectorY[i])]) is True
        p.add_beam(Beam(f"bmY_n{i}_v", **d2, up=vectorY[i]))

        # Along Z-Axis
        bm = p.add_beam(Beam(f"bmZ_n{i}_a", **d3, angle=angle))
        assert all([x == y for x, y in zip(bm.up, vectorZ[i])]) is True
        p.add_beam(Beam(f"bmZ_n{i}_v", **d3, up=vectorZ[i]))

    # # Visual Check
    # p.to_stp(test_folder / "my_angle_rotated_profiles.stp")
    # a.to_ifc(test_folder / "my_angle_rotated_profiles.ifc")


def test_beam_directions():
    sec = "HP200x10"

    beams = [
        Beam("bm_test2X0", n1=[0, 0, 0], n2=[5, 0, 0], angle=0, sec=sec),
        Beam("bm_test2X90", n1=[0, 0, 1], n2=[5, 0, 1], angle=90, sec=sec),
        Beam("bm_test2Y0", n1=[0, 0, 2], n2=[0, 5, 2], angle=0, sec=sec),
        Beam("bm_test2Y90", n1=[0, 0, 3], n2=[0, 5, 3], angle=90, sec=sec),
    ]
    a = Assembly("AdaRotatedProfiles") / (Part("Part") / beams)
    a.to_ifc(test_dir / "my_angled_profiles.ifc")
