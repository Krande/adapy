import ada


def test_basic_graph():
    bm1 = ada.Beam('bm1', (0, 0, 0), (1, 0, 0), 'IPE100')
    bm2 = ada.Beam('bm2', (0, 0, 0), (0, 1, 0), 'IPE100')
    a = ada.Assembly() / (ada.Part('MyPart') / [bm1, bm2])

    scene = a.to_trimesh_scene(merge_meshes=True)
    meta = scene.metadata

    draw_ranges_n0 = meta["draw_ranges_node0"]
    id_hierarchy = meta["id_hierarchy"]

    assert len(draw_ranges_n0) == 2
    assert len(id_hierarchy) == 4

    assert draw_ranges_n0["2"] == (0, 132)
    assert draw_ranges_n0["3"] == (132, 132)

    assert id_hierarchy["0"] == ('Ada', '*')
    assert id_hierarchy["1"] == ('MyPart', "0")
    assert id_hierarchy["2"] == ('bm1', "1")
    assert id_hierarchy["3"] == ('bm2', "1")


def test_basic_graph_multi_color():
    bm1 = ada.Beam('bm1', (0, 0, 0), (1, 0, 0), 'IPE100')
    bm2 = ada.Beam('bm2', (0, 0, 0), (0, 1, 0), 'IPE100', color='red')
    a = ada.Assembly() / (ada.Part('MyPart') / [bm1, bm2])

    scene = a.to_trimesh_scene(merge_meshes=True)
    meta = scene.metadata

    draw_ranges_n0 = meta["draw_ranges_node0"]
    assert draw_ranges_n0
    draw_ranges_n1 = meta["draw_ranges_node1"]
    assert draw_ranges_n1

    assert len(draw_ranges_n0) == 1
    assert len(draw_ranges_n1) == 1

    id_hierarchy = meta["id_hierarchy"]
    assert len(id_hierarchy) == 4

    assert draw_ranges_n0["2"] == (0, 132)
    assert draw_ranges_n1["3"] == (0, 132)

    assert id_hierarchy["0"] == ('Ada', '*')
    assert id_hierarchy["1"] == ('MyPart', "0")
    assert id_hierarchy["2"] == ('bm1', "1")
    assert id_hierarchy["3"] == ('bm2', "1")



