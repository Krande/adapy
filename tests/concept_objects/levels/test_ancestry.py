def test_parts_hierarchy(assembly_hierarchy, bm1, bm2):
    a = assembly_hierarchy

    list_of_ps = a.get_all_parts_in_assembly()

    assert len(list_of_ps) == 6

    p1 = a.get_by_name("my_part1")
    p21 = a.get_by_name("my_part2_subpart1")
    p3 = a.get_by_name("my_part3_subpart1")

    assert len(a.parts[p1.name].parts) == 2
    assert len(a.parts[p1.name].parts[p21.name].parts) == 1
    assert len(a.parts[p1.name].parts[p21.name].parts[p3.name].parts) == 1

    p3 / [bm1, bm2]

    beam_ancestry = bm1.get_ancestors()

    assert beam_ancestry[0] == bm1
    assert beam_ancestry[1] == p3
    assert beam_ancestry[2] == p21
    assert beam_ancestry[3] == p1
    assert beam_ancestry[4] == a
