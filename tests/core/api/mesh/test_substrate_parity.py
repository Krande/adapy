"""Parity + memory tests for the array-backed mesh substrate (MeshArrays).

The substrate must be behaviorally identical to the object-model
``Nodes``/``FemElements`` for the helper methods consumers rely on (especially
renumbering), and it must be dramatically lighter in memory. These tests build the
same mesh both ways and compare.
"""

import gc
import os

import numpy as np
import pytest

import ada
from ada.api.mesh.store import MeshArrays
from ada.api.nodes import Node
from ada.fem.shapes.definitions import ShellShapes


def _meshed_fem(size=0.5):
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.02)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(size, "shell")
    return p.fem


# ── construction / read parity ────────────────────────────────────────────


def test_from_fem_read_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)

    # node set parity (id -> coords), order-independent
    obj = {n.id: tuple(round(float(x), 9) for x in n.p) for n in fem.nodes}
    sub = {store.node_id(r): tuple(round(float(x), 9) for x in store.coords[r]) for r in range(store.n_nodes)}
    assert obj == sub

    # from_id parity for a sample
    for nid in list(obj)[:: max(1, len(obj) // 25)]:
        assert np.allclose(np.asarray(fem.nodes.from_id(nid).p), store.coords[store.node_index(nid)])

    # bbox parity
    assert np.allclose(np.asarray(fem.nodes.bbox()), np.asarray(store.bbox()))

    # to_fem_nodes parity (coords + identifiers)
    fn_obj = fem.nodes.to_fem_nodes()
    fn_sub = store.to_fem_nodes()
    # match rows by id then compare coords
    obj_by_id = {int(i): c for i, c in zip(fn_obj.identifiers, fn_obj.coords)}
    sub_by_id = {int(i): c for i, c in zip(fn_sub.identifiers, fn_sub.coords)}
    assert obj_by_id.keys() == sub_by_id.keys()
    for k in obj_by_id:
        assert np.allclose(obj_by_id[k], sub_by_id[k])


def test_element_block_connectivity_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)

    # every object element's node-id set must equal the block row resolved back to ids
    obj_elems = {e.id: tuple(n.id for n in e.nodes) for e in fem.elements.shell}
    sub_elems = {}
    for blk in store.blocks.values():
        for row in range(len(blk)):
            eid = int(blk.el_ids[row])
            sub_elems[eid] = tuple(store.node_id(idx) for idx in blk.conn[row])
    assert obj_elems == sub_elems


# ── mutation parity (the helper methods, incl. renumbering) ────────────────


def test_renumber_nodes_linear_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)

    fem.nodes.renumber(start_id=1)
    store.renumber_nodes(start_id=1)

    obj = {tuple(round(float(x), 9) for x in n.p): n.id for n in fem.nodes}
    sub = {tuple(round(float(x), 9) for x in store.coords[r]): store.node_id(r) for r in range(store.n_nodes)}
    assert obj == sub


def test_renumber_nodes_map_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)
    ids = sorted(n.id for n in fem.nodes)
    rmap = {old: old + 1000 for old in ids}

    fem.nodes.renumber(renumber_map=rmap)
    store.renumber_nodes(renumber_map=rmap)

    obj = {tuple(round(float(x), 9) for x in n.p): n.id for n in fem.nodes}
    sub = {tuple(round(float(x), 9) for x in store.coords[r]): store.node_id(r) for r in range(store.n_nodes)}
    assert obj == sub


def test_renumber_nodes_leaves_connectivity_valid():
    """After renumbering, each element still resolves to the same physical nodes."""
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)
    before = {int(b.el_ids[r]): b.conn[r].tolist() for b in store.blocks.values() for r in range(len(b))}
    store.renumber_nodes(start_id=1)
    after = {int(b.el_ids[r]): b.conn[r].tolist() for b in store.blocks.values() for r in range(len(b))}
    assert before == after  # conn is index-based -> untouched by node renumber


def test_translate_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)
    mv = np.array([1.5, -2.0, 3.25])

    fem.nodes.move(move=mv)
    store.translate(mv)

    obj = {n.id: np.asarray(n.p) for n in fem.nodes}
    for r in range(store.n_nodes):
        assert np.allclose(obj[store.node_id(r)], store.coords[r])


def test_box_query_parity():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)
    lo, hi = (1.0, 1.0, -0.1), (3.0, 2.0, 0.1)

    obj_ids = {n.id for n in fem.nodes if all(lo[i] <= n.p[i] <= hi[i] for i in range(3))}
    sub_ids = {store.node_id(r) for r in store.rows_in_box(lo, hi)}
    assert obj_ids == sub_ids


# ── proxy semantics ────────────────────────────────────────────────────────


def test_proxy_identity_and_equality():
    fem = _meshed_fem()
    store = MeshArrays.from_fem(fem)
    nid = sorted(n.id for n in fem.nodes)[len(list(fem.nodes)) // 2]

    a = store.node_proxy_by_id(nid)
    b = store.node_proxy_by_id(nid)
    assert a is b  # identity stable while alive
    assert isinstance(a, Node)
    assert a == fem.nodes.from_id(nid)  # value-equality with an object Node
    assert hash(a) == hash(Node(list(a.p), nid))


def test_proxy_write_through():
    store = MeshArrays.from_node_rows(np.array([[1, 0, 0, 0], [2, 1, 0, 0]], float))
    p = store.node_proxy_by_id(2)
    p.p = [5, 6, 7]
    assert np.allclose(store.coords[store.node_index(2)], [5, 6, 7])
    p.id = 99
    assert store.has_node(99) and not store.has_node(2)


# ── memory win (opt-in benchmark) ──────────────────────────────────────────


def _rss_mb():
    with open(f"/proc/{os.getpid()}/status") as f:
        for ln in f:
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) / 1024.0
    return 0.0


@pytest.mark.benchmark
def test_substrate_is_much_lighter_than_object_model():
    from ada.api.containers.nodes import Nodes
    from ada.fem.containers import FemElements
    from ada.fem.elements import Elem

    N = 200  # ~40k nodes / ~40k quads — enough to dwarf fixed overhead, fast to build
    xs, ys = np.meshgrid(np.arange(N), np.arange(N))
    coords = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(N * N)]).astype(float)
    node_ids = np.arange(1, N * N + 1, dtype=np.int64)

    def nid(i, j):
        return i * N + j + 1

    quads = [(nid(i, j), nid(i, j + 1), nid(i + 1, j + 1), nid(i + 1, j)) for i in range(N - 1) for j in range(N - 1)]
    quad_conn = np.array(quads, dtype=np.int64)
    quad_ids = np.arange(1, len(quads) + 1, dtype=np.int64)

    gc.collect()
    b0 = _rss_mb()
    node_objs = [Node(coords[k], int(node_ids[k])) for k in range(N * N)]
    nodes = Nodes(node_objs)
    elems = [
        Elem(int(quad_ids[e]), [nodes.from_id(int(c)) for c in quad_conn[e]], ShellShapes.QUAD)
        for e in range(len(quads))
    ]
    fem_elems = FemElements(elems)
    gc.collect()
    obj_mb = _rss_mb() - b0
    del node_objs, nodes, elems, fem_elems
    gc.collect()

    g0 = _rss_mb()
    store = MeshArrays(coords.copy(), node_ids.copy())
    store.add_elem_block_from_id_conn(ShellShapes.QUAD, quad_ids, quad_conn)
    gc.collect()
    arr_mb = _rss_mb() - g0

    ratio = obj_mb / max(arr_mb, 0.5)
    print(f"\nobject={obj_mb:.0f}MB substrate={arr_mb:.0f}MB ratio={ratio:.1f}x")
    assert ratio >= 5.0, f"expected >=5x memory reduction, got {ratio:.1f}x"


# ── structural edits + node->element adjacency ─────────────────────────────


def _two_quad_store():
    rows = np.array([[10, 0, 0, 0], [20, 1, 0, 0], [30, 1, 1, 0], [40, 0, 1, 0], [50, 2, 0, 0]], float)
    store = MeshArrays.from_node_rows(rows)
    store.add_elem_block_from_id_conn(ShellShapes.QUAD, [1, 2], [[10, 20, 30, 40], [20, 50, 30, 30]])
    return store


def test_csr_node_to_elem_adjacency():
    store = _two_quad_store()
    adj = store.node_to_elem()
    blk = store.blocks[ShellShapes.QUAD]
    # node 20 is shared by both quads
    inc = [int(blk.el_ids[r]) for _, r in adj.incident(store.node_index(20))]
    assert sorted(inc) == [1, 2]
    assert adj.degree(store.node_index(50)) == 1  # node 50 only in quad 2


def test_add_node_then_remove_keeps_connectivity_valid():
    store = _two_quad_store()
    before = {int(store.blocks[ShellShapes.QUAD].el_ids[0]): [10, 20, 30, 40]}
    r = store.add_node([9, 9, 9], nid=60)
    assert store.node_id(r) == 60 and store.has_node(60)
    store.remove_nodes([store.node_index(60)])  # 60 is unreferenced
    assert not store.has_node(60)
    conn0 = store.blocks[ShellShapes.QUAD].conn[0]
    resolved = {1: [store.node_id(x) for x in conn0]}
    assert resolved[1] == before[1]  # quad 1 still resolves to the same physical nodes


def test_extra_refs_side_table():
    store = _two_quad_store()
    row = store.node_index(10)
    marker = object()
    store.add_extra_ref(row, marker)
    assert store.extra_refs(row) == [marker]
    store.remove_extra_ref(row, marker)
    assert store.extra_refs(row) == []


# ── element proxies (ElemProxy / NodeListView / RefsView) ──────────────────


def test_elem_proxy_attributes():
    from ada.fem.elements import Elem

    store = _two_quad_store()
    store.blocks[ShellShapes.QUAD].fem_secs = ["secA", "secB"]
    e = store.elem_proxy_by_id(1)
    assert isinstance(e, Elem)
    assert e.id == 1 and e.type == ShellShapes.QUAD
    assert [n.id for n in e.nodes] == [10, 20, 30, 40]
    assert e.fem_sec == "secA"
    assert type(e.shape).__name__ == "ElemShape"
    assert store.elem_proxy_by_id(1) is e  # identity stable


def test_refsview_chains_elements_and_extras():
    store = _two_quad_store()
    n20 = store.node_proxy_by_id(20)  # shared by both quads
    assert sorted(x.id for x in n20.refs) == [1, 2]
    assert n20.has_refs

    n10 = store.node_proxy_by_id(10)
    n10.add_obj_to_refs("beamX")  # non-element ref -> side-table
    refs = list(n10.refs)
    assert refs[0].id == 1 and "beamX" in refs
    n10.remove_obj_from_refs("beamX")
    assert "beamX" not in list(n10.refs)


def test_updating_nodes_rewires_adjacency():
    store = _two_quad_store()
    e1 = store.elem_proxy_by_id(1)
    e1.updating_nodes(store.node_proxy_by_id(40), store.node_proxy_by_id(50))
    assert [n.id for n in e1.nodes] == [10, 20, 30, 50]
    assert [x.id for x in store.node_proxy_by_id(40).refs] == []  # 40 no longer referenced
    assert sorted(x.id for x in store.node_proxy_by_id(50).refs) == [1, 2]


def test_elem_node_setitem_write_through():
    store = _two_quad_store()
    e = store.elem_proxy_by_id(1)
    e.nodes[3] = store.node_proxy_by_id(50)
    assert store.blocks[ShellShapes.QUAD].conn[0].tolist() == [
        store.node_index(10),
        store.node_index(20),
        store.node_index(30),
        store.node_index(50),
    ]


# ── facade (ArrayNodes / ArrayElements) wired into a FEM ────────────────────


def test_to_array_backed_swaps_facades_with_parity():
    from ada.api.mesh.containers import ArrayElements, ArrayNodes, to_array_backed

    fem = _meshed_fem()
    obj_nodes = {n.id: tuple(round(float(x), 9) for x in n.p) for n in fem.nodes}
    obj_shell = {e.id: tuple(n.id for n in e.nodes) for e in fem.elements.shell}

    to_array_backed(fem)
    assert isinstance(fem.nodes, ArrayNodes) and isinstance(fem.elements, ArrayElements)

    assert {n.id: tuple(round(float(x), 9) for x in n.p) for n in fem.nodes} == obj_nodes
    assert {e.id: tuple(n.id for n in e.nodes) for e in fem.elements.shell} == obj_shell
    # a shell element resolves fem_sec (thickness) through the block
    e0 = next(iter(fem.elements.shell))
    assert e0.fem_sec is not None and e0.fem_sec.thickness == 0.02


def test_array_backed_conversion_matches_object_path():
    """create_objects_from_fem must yield identical plates on an array-backed FEM."""
    import ada
    from ada.api.mesh.containers import to_array_backed

    def _plate_cogs(array_backed):
        pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.02)
        p = ada.Part("p") / pl
        p.fem = pl.to_fem_obj(0.5, "shell")
        if array_backed:
            to_array_backed(p.fem)
        a = ada.Assembly("a") / p
        a.create_objects_from_fem(merge=False)
        plates = list(p.get_all_physical_objects(by_type=ada.Plate))
        return sorted(tuple(round(float(x), 6) for x in pl.poly.get_centroid()) for pl in plates)

    assert _plate_cogs(False) == _plate_cogs(True)


def test_array_nodes_helpers_parity():
    from ada.api.mesh.containers import to_array_backed

    obj = _meshed_fem()
    arr = to_array_backed(_meshed_fem())

    # renumber (linear) -> same coord->id mapping
    obj.nodes.renumber(start_id=1)
    arr.nodes.renumber(start_id=1)
    om = {tuple(round(float(x), 9) for x in n.p): n.id for n in obj.nodes}
    am = {tuple(round(float(x), 9) for x in n.p): n.id for n in arr.nodes}
    assert om == am

    # move -> same coords
    obj.nodes.move(move=[1, 2, 3])
    arr.nodes.move(move=[1, 2, 3])
    assert {n.id: tuple(round(float(x), 9) for x in n.p) for n in obj.nodes} == {
        n.id: tuple(round(float(x), 9) for x in n.p) for n in arr.nodes
    }

    # box query -> same id set
    obj_ids = {n.id for n in obj.nodes.get_by_volume((2, 3, 3), tol=1.0)}
    arr_ids = {n.id for n in arr.nodes.get_by_volume((2, 3, 3), tol=1.0)}
    assert obj_ids == arr_ids
