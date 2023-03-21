from ada import Assembly, Beam, Part


def test_beam_rotation_by_angle(test_dir):
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
    # p.to_stp(test_dir / "my_angle_rotated_profiles.stp")
    # a.to_ifc(test_dir / "my_angle_rotated_profiles.ifc")
