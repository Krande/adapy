from ada import Assembly, Beam, Part, Plate, Section, User


def test_export_basic(ifc_test_dir):
    bm = Beam(
        "MyBeam",
        (0, 0, 0),
        (2, 0, 0),
        Section("MySec", from_str="BG300x200x10x20"),
        metadata=dict(hidden=True),
    )
    bm1 = Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red")
    bm2 = Beam("bm2", n1=[0, 0, 0], n2=[0, 2, 0], sec="IPE220", color="blue")
    bm3 = Beam("bm3", n1=[0, 0, 0], n2=[0, 0, 2], sec="IPE220", color="green")
    bm4 = Beam("bm4", n1=[0, 0, 0], n2=[2, 0, 2], sec="IPE220", color="gray")
    bm5 = Beam("bm5", n1=[0, 0, 2], n2=[2, 0, 2], sec="IPE220", color="white")

    user = User(user_id="krande", org_id="ADA", org_name="Assembly Test")
    pl1 = Plate(
        "pl1",
        [(0, 0, 0), (0, 0, 2), (0, 2, 2), (0, 2.0, 0.0)],
        0.01,
        use3dnodes=True,
    )

    a = Assembly("MyFirstIfcFile", user=user) / (
        Part("MyBldg", metadata=dict(ifctype="building")) / [bm, bm1, bm2, bm3, bm4, bm5, pl1]
    )

    a.to_ifc(ifc_test_dir / "my_test.ifc", file_obj_only=True)


def test_ifc_groups(ifc_test_dir):
    a = Assembly("MySiteName", project="MyTestProject")
    p = Part(
        "MyTopSpatialLevel",
        metadata=dict(ifctype="spatial", description="MyTopLevelSpace"),
    )
    p.add_beam(Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red"))
    a.add_part(p)

    newp = Part(
        "MySecondLevel",
        metadata=dict(ifctype="spatial", description="MySecondLevelSpace"),
    )
    newp.add_beam(Beam("bm2", n1=[0, 0, 0], n2=[0, 2, 0], sec="IPE220", color="blue"))
    p.add_part(newp)

    newp2 = Part(
        "MyThirdLevel",
        metadata=dict(ifctype="spatial", description="MyThirdLevelSpace"),
    )
    newp2.add_beam(Beam("bm3", n1=[0, 0, 0], n2=[0, 0, 2], sec="IPE220", color="green"))
    newp2.add_plate(
        Plate(
            "pl1",
            [(0, 0, 0), (0, 0, 2), (0, 2, 2), (0, 2.0, 0.0)],
            0.01,
            use3dnodes=True,
        )
    )
    newp.add_part(newp2)

    _ = a.to_ifc(ifc_test_dir / "my_test_groups.ifc", file_obj_only=True)


def test_profiles_to_ifc(ifc_test_dir):
    a = Assembly("MyAssembly")
    p = Part("MyPart")
    p.add_beam(Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red"))
    p.add_beam(Beam("bm2", n1=[0, 0, 1], n2=[2, 0, 1], sec="HP220x10", color="blue"))
    p.add_beam(Beam("bm3", n1=[0, 0, 2], n2=[2, 0, 2], sec="BG800x400x20x40", color="green"))
    p.add_beam(Beam("bm4", n1=[0, 0, 3], n2=[2, 0, 3], sec="CIRC200", color="green"))
    p.add_beam(Beam("bm5", n1=[0, 0, 4], n2=[2, 0, 4], sec="TUB200x10", color="green"))
    a.add_part(p)
    _ = a.to_ifc(ifc_test_dir / "my_beam_profiles.ifc", file_obj_only=True)
