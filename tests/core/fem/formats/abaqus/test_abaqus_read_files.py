import ada


def test_read_C3D20(example_files):
    a = ada.from_fem(example_files / "fem_files/abaqus/box.inp")
    assert len(a.parts) == 1
    p = a.parts["box"]
    assert len(p.fem.nodes) == 2651
    assert len(p.fem.elements) == 500


def test_read_R3D4(example_files):
    a = ada.from_fem(example_files / "fem_files/abaqus/box_rigid.inp")
    assert len(a.fem.constraints.values()) == 1
