from ada import Assembly, Beam, Part, PrimCyl


def test_beam_mesh(test_meshing_dir):
    bm = Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")
    p = Part("MyFem") / bm
    bm.add_penetration(PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))
    p.fem = bm.to_fem_obj(0.5, "line")

    assert len(p.fem.elements) == 2
    assert len(p.fem.nodes) == 3

    (Assembly("Test") / p).to_ifc(test_meshing_dir / "bm_mesh_ifc", include_fem=True)
