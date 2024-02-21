import ada


def test_line_part_offset():
    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    p = ada.Part("Part", placement=ada.Placement((200, 100, 500))) / bm
    fem = p.to_fem_obj(1)
    assert len(list(fem.elements.lines)) == 1
    assert len(fem.nodes) == 2
    p1 = fem.nodes[0].p
    p2 = fem.nodes[1].p

    assert (bm.n1.p + p.placement.origin).is_equal(p1)
    assert (bm.n2.p + p.placement.origin).is_equal(p2)
