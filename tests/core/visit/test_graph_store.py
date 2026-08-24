import pytest

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


# --------------------------------------------------------------------------- #
# Stable keys on the hierarchy
#
# ``id_hierarchy`` carries (name, parent) and drops every stable identity, so any
# consumer wanting to say "this node and that node in another model are the same
# thing" has to fall back on the display name. A node can now carry an opaque,
# domain-qualified ``stable_key``, emitted as a *parallel* extras map. Parallel and
# not a third tuple element: every existing reader of the tuple — here and in the
# native writers outside this repo — keeps working with no audit.
# --------------------------------------------------------------------------- #
KEY_DOMAIN = "example-domain"
KEY_BM1 = f"{KEY_DOMAIN}:/SOME-ELEMENT-NAME"
KEY_BM2 = f"{KEY_DOMAIN}:/SOME-OTHER-ELEMENT-NAME"


def _two_beam_store():
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE100")
    bm2 = ada.Beam("bm2", (0, 0, 0), (0, 1, 0), "IPE100")
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])
    return a, a.get_graph_store()


def _node_by_name(store, name):
    return next(n for n in store.nodes.values() if n.name == name)


def test_no_stable_keys_emits_exactly_the_previous_contract():
    """The no-keys export must be indistinguishable from the pre-keys one — same
    keys, same order, same values — so every GLB produced without keys is byte-for-byte
    what it was before."""
    _, store = _two_beam_store()

    data = store.to_json_hierarchy()

    assert list(data.keys()) == ["id_hierarchy"]
    assert data["id_hierarchy"] == {
        "0": ("Ada", "*"),
        "1": ("MyPart", "0"),
        "2": ("bm1", "1"),
        "3": ("bm2", "1"),
    }


def test_no_stable_keys_leaves_no_trace_in_the_exported_scene():
    a, _ = _two_beam_store()

    meta = a.to_trimesh_scene(merge_meshes=True).metadata

    assert "node_keys" not in meta
    assert "node_key_domain" not in meta
    assert "id_hierarchy" in meta and "draw_ranges_node0" in meta


def test_stable_keys_round_trip_alongside_an_untouched_id_hierarchy():
    a, store = _two_beam_store()
    store.key_domain = KEY_DOMAIN
    _node_by_name(store, "bm1").stable_key = KEY_BM1
    _node_by_name(store, "bm2").stable_key = KEY_BM2

    data = store.to_json_hierarchy()

    assert data["node_keys"] == {"2": KEY_BM1, "3": KEY_BM2}
    assert data["node_key_domain"] == KEY_DOMAIN
    # the tuple is still (name, parent) — nothing widened it
    assert all(len(v) == 2 for v in data["id_hierarchy"].values())
    assert data["id_hierarchy"] == a.get_graph_store().to_json_hierarchy()["id_hierarchy"]


def test_only_keyed_nodes_appear_in_the_key_map():
    """A partially keyed graph emits only the keys that exist. Absence of a key is a
    real state — "no stable identity" — not something to be papered over."""
    _, store = _two_beam_store()
    store.key_domain = KEY_DOMAIN
    _node_by_name(store, "bm2").stable_key = KEY_BM2

    data = store.to_json_hierarchy()

    assert data["node_keys"] == {"3": KEY_BM2}
    assert len(data["id_hierarchy"]) == 4


def test_key_domain_is_omitted_when_the_store_declares_none():
    _, store = _two_beam_store()
    _node_by_name(store, "bm1").stable_key = KEY_BM1

    data = store.to_json_hierarchy()

    assert data["node_keys"] == {"2": KEY_BM1}
    assert "node_key_domain" not in data


def test_assign_stable_keys_from_hash_keeps_the_guid_that_was_being_dropped():
    """``GraphNode.hash`` is the object guid and the natural identity for objects adapy
    made itself, yet ``to_json_hierarchy`` threw it away. Opting in keeps it."""
    a, store = _two_beam_store()

    keyed = store.assign_stable_keys_from_hash("ada.guid")
    data = store.to_json_hierarchy()

    assert keyed == len(store.nodes)
    assert data["node_key_domain"] == "ada.guid"
    assert data["node_keys"]["2"] == a.get_by_name("bm1").guid
    assert data["node_keys"]["3"] == a.get_by_name("bm2").guid


def test_existing_glb_readers_ignore_the_new_extras_keys():
    """diff.parse_elements and diagnostics.glb_parts both read the same scene extras.
    Both select what they want by name/prefix, so an added parallel map is inert to
    them — asserted rather than assumed."""
    diff = pytest.importorskip("ada.comms.rest.utilities.diff")
    from ada.cadit.diagnostics import glb_parts

    a, _ = _two_beam_store()
    scene = a.to_trimesh_scene(merge_meshes=True)
    scene.metadata["node_keys"] = {"2": KEY_BM1, "3": KEY_BM2}
    scene.metadata["node_key_domain"] = KEY_DOMAIN
    glb = scene.export(file_type="glb")

    assert sorted(diff.parse_elements(glb)) == ["bm1", "bm2"]
    assert sorted(p.name for p in glb_parts(glb)) == ["bm1", "bm2"]
