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
