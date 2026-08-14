import ada


def test_basic_graph():
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE100")
    bm2 = ada.Beam("bm2", (0, 0, 0), (0, 1, 0), "IPE100")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

    scene = a.to_trimesh_scene(merge_meshes=True)
    meta = scene.metadata

    draw_ranges_n0 = meta["draw_ranges_node0"]
    id_hierarchy = meta["id_hierarchy"]

    assert len(draw_ranges_n0) == 2
    assert len(id_hierarchy) == 4

    assert draw_ranges_n0["2"] == (0, 132)
    assert draw_ranges_n0["3"] == (132, 132)

    assert id_hierarchy["0"] == ("Ada", "*")
    assert id_hierarchy["1"] == ("MyPart", "0")
    assert id_hierarchy["2"] == ("bm1", "1")
    assert id_hierarchy["3"] == ("bm2", "1")


def test_basic_graph_multi_color():
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE100")
    bm2 = ada.Beam("bm2", (0, 0, 0), (0, 1, 0), "IPE100", color="red")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])

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

    assert id_hierarchy["0"] == ("Ada", "*")
    assert id_hierarchy["1"] == ("MyPart", "0")
    assert id_hierarchy["2"] == ("bm1", "1")
    assert id_hierarchy["3"] == ("bm2", "1")


def test_pipe_segments_nest_under_pipe_not_orphaned_to_root():
    """A Pipe expands into segment meshes for tessellation (pipe_to_segments), but
    the segments' parent is the Pipe itself — which the walk skips. Without
    materialising that Pipe container node, every segment orphans to the scene root
    ("*") and the whole run flattens out of the selection tree. Assert the Pipe is a
    node under its part and its segments nest under the Pipe."""
    sec = ada.Section("PSec", "PIPE", r=0.05, wt=5e-3)
    pipe = ada.Pipe("P1", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], sec)
    a = ada.Assembly() / (ada.Part("Sys") / pipe)

    id_hierarchy = a.to_trimesh_scene(merge_meshes=True).metadata["id_hierarchy"]
    by_name = {v[0]: (k, v[1]) for k, v in id_hierarchy.items()}

    # exactly one root: the assembly
    roots = [k for k, v in id_hierarchy.items() if v[1] == "*"]
    assert len(roots) == 1

    assert "P1" in by_name, "Pipe container node missing"
    pipe_id, pipe_parent = by_name["P1"]
    sys_id = by_name["Sys"][0]
    assert pipe_parent == sys_id, "Pipe must nest under its part, not the root"

    # every pipe segment nests under the Pipe (none orphaned to '*')
    seg_parents = {v[1] for name, v in by_name.items() if name.startswith("P1_")}
    assert seg_parents == {pipe_id}, f"pipe segments not nested under the pipe: {seg_parents}"
