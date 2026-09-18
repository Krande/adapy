"""Element node ordering, and conversion between adapy's ordering and a format's.

adapy's **native** ordering is Abaqus': corner nodes first, then one mid-side node
per edge. For every shape except ``LINE3`` that is also the VTK / meshio ordering.
``LINE3`` is the exception on purpose -- native is ``(end, mid, end)``, matching
Abaqus B32, where VTK / meshio / gmsh all use ``(end, end, mid)``.

Because of that exception the tables below never assume "corners are the leading
slots": :data:`NATIVE_CORNERS` names the corner slots outright and
:data:`NATIVE_MIDSIDE_EDGES` maps each mid-side slot to the two corner slots it
sits between. Every reader is expected to hand the mesh over in native ordering
and every writer to emit its format's, so the in-memory mesh has one convention.

A format states how it differs in its own ``node_order.py`` as a
:class:`NodeOrder`, holding one permutation per shape. Conversion is a single
gather over a whole element block::

    conn_out = conn[:, perm]

which is why the permutations are expressed against the array-backed
connectivity rather than per element: reordering a block of 350k tetrahedra is
one numpy fancy-index, not 350k Python loops.

``perm`` reads "native index for each slot of the target ordering", so
``conn[:, perm][:, k]`` is the node the target format wants in its slot ``k``.
:func:`invert` turns that around for the read direction.

The gmsh mesher has the same relationship to native, expressed separately as
``mesh_types.gmsh_to_meshio_ordering`` and applied in ``fem.meshing.utils``;
it predates this module and is left where it is.
"""

from __future__ import annotations

import numpy as np

from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

#: Corner (vertex) slots of each second-order shape, in native ordering. Named
#: explicitly rather than implied as "the first N slots", because ``LINE3`` puts
#: its mid node between the two ends.
NATIVE_CORNERS: dict = {
    LineShapes.LINE3: (0, 2),
    ShellShapes.TRI6: (0, 1, 2),
    ShellShapes.QUAD8: (0, 1, 2, 3),
    ShellShapes.QUAD9: (0, 1, 2, 3),
    SolidShapes.TETRA10: (0, 1, 2, 3),
    SolidShapes.WEDGE15: (0, 1, 2, 3, 4, 5),
    SolidShapes.HEX20: (0, 1, 2, 3, 4, 5, 6, 7),
}

#: ``{mid-side slot: (corner slot, corner slot)}`` per shape, in native ordering.
#: This *is* the definition of adapy's native ordering for second-order shapes,
#: and :func:`derive_midside_edges` reads the same relation back off real
#: coordinates -- so a table can be checked against a deck rather than trusted.
#: The solid and shell entries were checked against MEDCoupling, by exploding each
#: quadratic cell into its edges and reading the (corner, corner, mid) triples back.
NATIVE_MIDSIDE_EDGES: dict = {
    LineShapes.LINE3: {1: (0, 2)},
    ShellShapes.TRI6: {3: (0, 1), 4: (1, 2), 5: (2, 0)},
    ShellShapes.QUAD8: {4: (0, 1), 5: (1, 2), 6: (2, 3), 7: (3, 0)},
    ShellShapes.QUAD9: {4: (0, 1), 5: (1, 2), 6: (2, 3), 7: (3, 0)},
    SolidShapes.TETRA10: {4: (0, 1), 5: (1, 2), 6: (0, 2), 7: (0, 3), 8: (1, 3), 9: (2, 3)},
    SolidShapes.WEDGE15: {
        6: (0, 1),
        7: (1, 2),
        8: (2, 0),
        9: (3, 4),
        10: (4, 5),
        11: (5, 3),
        12: (0, 3),
        13: (1, 4),
        14: (2, 5),
    },
    SolidShapes.HEX20: {
        8: (0, 1),
        9: (1, 2),
        10: (2, 3),
        11: (3, 0),
        12: (4, 5),
        13: (5, 6),
        14: (6, 7),
        15: (7, 4),
        16: (0, 4),
        17: (1, 5),
        18: (2, 6),
        19: (3, 7),
    },
}


def num_nodes(ctype) -> int:
    """Node count of a second-order shape, from its own tables."""
    return len(NATIVE_CORNERS[ctype]) + len(NATIVE_MIDSIDE_EDGES[ctype])


def invert(perm) -> tuple[int, ...]:
    """The permutation that undoes ``perm``."""
    out = [0] * len(perm)
    for slot, native in enumerate(perm):
        out[native] = slot
    return tuple(out)


class NodeOrder:
    """One FEM format's element node ordering, relative to adapy native.

    Shapes absent from ``native_to_format`` are ordered the same as native, so a
    format only declares where it actually differs.
    """

    __slots__ = ("name", "_to_format", "_from_format")

    def __init__(self, name: str, native_to_format: dict | None = None):
        self.name = name
        self._to_format = dict(native_to_format or {})
        for ctype, perm in self._to_format.items():
            if sorted(perm) != sorted(range(len(perm))):
                raise ValueError(f"{name}: {ctype} permutation {perm} is not a permutation of 0..{len(perm) - 1}")
            if ctype in NATIVE_MIDSIDE_EDGES and len(perm) != num_nodes(ctype):
                raise ValueError(f"{name}: {ctype} permutation has {len(perm)} slots, expected {num_nodes(ctype)}")
        self._from_format = {c: invert(p) for c, p in self._to_format.items()}

    def to_format(self, ctype) -> tuple[int, ...] | None:
        """Native -> this format, or None when the orderings agree."""
        return self._to_format.get(ctype)

    def from_format(self, ctype) -> tuple[int, ...] | None:
        """This format -> native, or None when the orderings agree."""
        return self._from_format.get(ctype)

    def conn_to_format(self, ctype, conn: np.ndarray) -> np.ndarray:
        """Reorder a whole ``(m, k)`` connectivity block into this format."""
        perm = self.to_format(ctype)
        return conn if perm is None else conn[:, perm]

    def conn_from_format(self, ctype, conn: np.ndarray) -> np.ndarray:
        """Reorder a whole ``(m, k)`` connectivity block into native order."""
        perm = self.from_format(ctype)
        return conn if perm is None else conn[:, perm]

    def nodes_to_format(self, ctype, nodes: list) -> list:
        """Per-element form of :meth:`conn_to_format`, for the object mesh path."""
        perm = self.to_format(ctype)
        if perm is None or len(nodes) != len(perm):
            return nodes
        return [nodes[i] for i in perm]

    def nodes_from_format(self, ctype, nodes: list) -> list:
        perm = self.from_format(ctype)
        if perm is None or len(nodes) != len(perm):
            return nodes
        return [nodes[i] for i in perm]

    def __repr__(self) -> str:
        return f"NodeOrder({self.name!r}, {len(self._to_format)} shape(s) reordered)"


#: adapy's own ordering, i.e. no permutation at all. Handy as a default and as
#: the thing a format's table is defined against.
NATIVE_ORDER = NodeOrder("native")


def derive_midside_edges(conn: np.ndarray, coords: np.ndarray, ctype, rtol: float = 1e-4) -> dict:
    """Read a mesh's mid-side convention back off its geometry.

    For each non-corner slot, finds the corner pair whose midpoint it sits on across
    the sampled elements, and returns ``{slot: (i, j)}`` in the same shape as
    :data:`NATIVE_MIDSIDE_EDGES` -- so a table can be compared against what a file
    actually contains instead of assumed.

    ``corners`` defaults to the native corner slots; pass a format's own corner
    slots to read that format's file in its own terms. Second-order elements on
    curved geometry carry mid-side nodes deliberately off the straight-line
    midpoint, so this votes across elements rather than demanding every one agree;
    slots with no clear winner are left out.
    """
    import itertools

    if ctype not in NATIVE_MIDSIDE_EDGES:
        return {}
    corners = NATIVE_CORNERS[ctype]
    p = coords[conn]  # (m, k, 3)
    out = {}
    for slot in range(conn.shape[1]):
        if slot in corners:
            continue
        best, best_hits = None, 0
        for i, j in itertools.combinations(corners, 2):
            span = np.linalg.norm(p[:, i] - p[:, j], axis=1)
            off = np.linalg.norm(p[:, slot] - 0.5 * (p[:, i] + p[:, j]), axis=1)
            hits = int((off <= rtol * np.maximum(span, 1e-12)).sum())
            if hits > best_hits:
                best, best_hits = (i, j), hits
        # a real winner should account for most of the sample, not just edge out
        # the runners-up on a handful of elements
        if best is not None and best_hits > 0.5 * conn.shape[0]:
            out[slot] = best
    return out
