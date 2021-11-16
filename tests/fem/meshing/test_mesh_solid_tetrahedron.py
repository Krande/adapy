import ada


def test_beam_mesh_with_hole(test_meshing_dir):
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")
    p = ada.Part("MyFem") / bm
    bm.add_penetration(ada.PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))
    p.fem = bm.to_fem_obj(0.5, "line")

    el_types = {el_type: list(group) for el_type, group in p.fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["LINE"]) == 2

    assert len(p.fem.nodes) == 3

    # (Assembly("Test") / p).to_ifc(test_meshing_dir / "bm_mesh_ifc", include_fem=True)
