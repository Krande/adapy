import ada


def test_basic_plate(pl1, test_meshing_dir):
    p = ada.Part("MyFem") / pl1
    p.fem = pl1.to_fem_obj(5, "shell")

    el_types = {el_type: list(group) for el_type, group in p.fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["TRIANGLE"]) == 8

    # (ada.Assembly("Test") / p).to_ifc(test_meshing_dir / "ADA_pl_mesh_ifc", include_fem=False)
