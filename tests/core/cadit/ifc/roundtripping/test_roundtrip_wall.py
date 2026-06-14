import ada


def test_roundtrip_wall(tmp_path):
    points = [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
    wall = ada.Wall("MyWall", points, height=3.0, thickness=0.15, offset="LEFT")

    fp = (ada.Assembly() / (ada.Part("MyPart") / wall)).to_ifc(tmp_path / "wall.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    w: ada.Wall = a.get_by_name("MyWall")

    assert isinstance(w, ada.Wall)
    assert w.parent.name == "MyPart"
    assert w.height == 3.0
    assert w.thickness == 0.15
    # offset="LEFT" resolves to -thickness/2
    assert w.offset == -0.15 / 2
    # centerline points preserved (2D padded to 3D)
    assert [tuple(p) for p in w.points] == [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
