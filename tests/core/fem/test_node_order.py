"""Element node ordering: the native convention, and each format's permutation of it.

The failure these guard against is silent — a wrong permutation still writes a
well-formed deck, it just puts corner and mid-side nodes in each other's slots, so
the solver sees distorted elements. Nothing else in the suite catches it: the
array-parity tests compare ``sorted(...)`` node ids, which is permutation-blind.
"""

import numpy as np
import pytest

from ada.fem.formats.abaqus.node_order import ABAQUS_ORDER
from ada.fem.formats.calculix.node_order import CALCULIX_ORDER
from ada.fem.formats.code_aster.node_order import CODE_ASTER_ORDER
from ada.fem.formats.sesam.node_order import SESAM_ORDER
from ada.fem.formats.usfos.node_order import USFOS_ORDER
from ada.fem.shapes.definitions import ShellShapes, SolidShapes
from ada.fem.shapes.node_order import (
    NATIVE_MIDSIDE_EDGES,
    NUM_CORNERS,
    NodeOrder,
    derive_midside_edges,
    invert,
)

ALL_ORDERS = [ABAQUS_ORDER, CALCULIX_ORDER, CODE_ASTER_ORDER, SESAM_ORDER, USFOS_ORDER]


def test_invert_round_trips():
    perm = (0, 4, 1, 5, 2, 6, 7, 8, 9, 3)
    assert invert(invert(perm)) == perm
    assert tuple(perm[i] for i in invert(perm)) == tuple(range(len(perm)))


def test_a_non_permutation_is_rejected():
    with pytest.raises(ValueError, match="not a permutation"):
        NodeOrder("bad", {SolidShapes.TETRA10: (0, 0, 1, 2, 3, 4, 5, 6, 7, 8)})


@pytest.mark.parametrize("order", ALL_ORDERS, ids=lambda o: o.name)
def test_every_declared_permutation_is_self_consistent(order):
    """to_format and from_format must undo each other, and cover the shape's nodes."""
    for ctype in list(NATIVE_MIDSIDE_EDGES) + [SolidShapes.TETRA, SolidShapes.HEX8]:
        fwd = order.to_format(ctype)
        if fwd is None:
            continue
        assert order.from_format(ctype) == invert(fwd)
        n = NUM_CORNERS[ctype] + len(NATIVE_MIDSIDE_EDGES[ctype])
        assert sorted(fwd) == list(range(n)), f"{order.name}/{ctype} does not cover {n} nodes"


@pytest.mark.parametrize("order", ALL_ORDERS, ids=lambda o: o.name)
def test_conn_round_trips_through_a_format(order):
    rng = np.random.default_rng(0)
    for ctype in NATIVE_MIDSIDE_EDGES:
        n = NUM_CORNERS[ctype] + len(NATIVE_MIDSIDE_EDGES[ctype])
        conn = rng.integers(0, 1000, size=(7, n))
        assert np.array_equal(order.conn_from_format(ctype, order.conn_to_format(ctype, conn)), conn)


def test_conn_to_format_is_a_single_gather():
    """The permutation has to apply to a whole block at once — that is the point of
    expressing it against the array-backed connectivity."""
    conn = np.arange(20 * 10).reshape(20, 10)
    out = SESAM_ORDER.conn_to_format(SolidShapes.TETRA10, conn)
    assert out.shape == conn.shape
    perm = SESAM_ORDER.to_format(SolidShapes.TETRA10)
    for row in range(conn.shape[0]):
        assert list(out[row]) == [conn[row][i] for i in perm]


# ── the Sesam tables, against the figures they came from ──────────────────────


def test_sesam_interleaves_the_isoparametric_solids_and_scqs():
    """Corners land on every other slot; SCTS is the documented exception."""
    for ctype, n_corner in [
        (SolidShapes.TETRA10, 3),  # 3 base corners interleaved, apex last
        (SolidShapes.WEDGE15, 3),
        (SolidShapes.HEX20, 4),
        (ShellShapes.QUAD8, 4),
    ]:
        perm = SESAM_ORDER.to_format(ctype)
        assert perm is not None, f"{ctype} should be reordered for sesam"
        corners_in_first_slots = perm[: 2 * n_corner : 2]
        assert list(corners_in_first_slots) == list(range(n_corner)), ctype

    # SCTS (figure 5-25) is corners-then-midsides, i.e. native
    assert SESAM_ORDER.to_format(ShellShapes.TRI6) is None


def test_sesam_tetra10_matches_figure_5_31():
    """ITET: corners at local 1, 3, 5 and the apex at 10; mid-sides 2/4/6 round the
    base and 7/8/9 up to the apex."""
    perm = SESAM_ORDER.to_format(SolidShapes.TETRA10)
    edges = NATIVE_MIDSIDE_EDGES[SolidShapes.TETRA10]
    n_corner = NUM_CORNERS[SolidShapes.TETRA10]

    def edge_at(slot):
        """The native corner pair the node in sesam slot `slot` (0-based) bisects."""
        native = perm[slot]
        return tuple(sorted(edges[native - n_corner]))

    corner_native = {0: perm[0], 2: perm[2], 4: perm[4], 9: perm[9]}
    assert sorted(corner_native.values()) == [0, 1, 2, 3], "slots 1,3,5,10 must be the corners"
    a, b, c, d = corner_native[0], corner_native[2], corner_native[4], corner_native[9]
    assert edge_at(1) == tuple(sorted((a, b)))
    assert edge_at(3) == tuple(sorted((b, c)))
    assert edge_at(5) == tuple(sorted((c, a)))
    assert edge_at(6) == tuple(sorted((a, d)))
    assert edge_at(7) == tuple(sorted((b, d)))
    assert edge_at(8) == tuple(sorted((c, d)))


def test_formats_that_match_native_declare_nothing():
    for order in (ABAQUS_ORDER, CALCULIX_ORDER, CODE_ASTER_ORDER, USFOS_ORDER):
        for ctype in NATIVE_MIDSIDE_EDGES:
            assert order.to_format(ctype) is None, f"{order.name} unexpectedly reorders {ctype}"


# ── the geometric check that lets a table be verified against a real mesh ─────


def _unit_tet10():
    """A straight-sided tet10 in native ordering: corners then mid-sides."""
    corners = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    mids = [0.5 * (corners[i] + corners[j]) for i, j in NATIVE_MIDSIDE_EDGES[SolidShapes.TETRA10]]
    coords = np.vstack([corners, np.array(mids)])
    return coords, np.arange(10).reshape(1, 10)


def test_derive_midside_edges_recovers_the_native_convention():
    coords, conn = _unit_tet10()
    got = derive_midside_edges(conn, coords, SolidShapes.TETRA10)
    want = {4 + i: e for i, e in enumerate(NATIVE_MIDSIDE_EDGES[SolidShapes.TETRA10])}
    assert {k: tuple(sorted(v)) for k, v in got.items()} == {k: tuple(sorted(v)) for k, v in want.items()}


def test_derive_midside_edges_sees_through_a_sesam_permuted_element():
    """Permute a known element into Sesam order; reading the convention back off the
    coordinates must show Sesam's edges, not native's."""
    coords, conn = _unit_tet10()
    sesam_conn = SESAM_ORDER.conn_to_format(SolidShapes.TETRA10, conn)
    got = derive_midside_edges(sesam_conn, coords, SolidShapes.TETRA10)
    # In Sesam ordering slots 0,2,4,9 are corners, so the "corner" slots the helper
    # assumes (0-3) no longer all are — what matters is that it does NOT come back
    # looking like the native convention.
    want_native = {4 + i: tuple(sorted(e)) for i, e in enumerate(NATIVE_MIDSIDE_EDGES[SolidShapes.TETRA10])}
    assert {k: tuple(sorted(v)) for k, v in got.items()} != want_native
