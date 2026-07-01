"""Robust boundary extraction (outer + holes) from a set of facet loops."""

from ada.core.vector_utils import extract_boundary_loops


def _grid_quads(nx, ny, skip=()):
    """(nx*ny) CCW unit quads on the z=0 grid, omitting the cells in ``skip``."""

    def node(i, j):
        return (float(i), float(j), 0.0)

    return [
        [node(i, j), node(i + 1, j), node(i + 1, j + 1), node(i, j + 1)]
        for i in range(nx)
        for j in range(ny)
        if (i, j) not in skip
    ]


def test_square_one_outer_no_holes():
    r = extract_boundary_loops(_grid_quads(2, 2))
    assert r is not None
    outer, holes = r
    assert holes == []
    assert len(outer) == 4  # collinear boundary points removed to the 4 corners


def test_square_with_central_hole():
    r = extract_boundary_loops(_grid_quads(3, 3, skip={(1, 1)}))
    assert r is not None
    outer, holes = r
    assert len(holes) == 1
    assert len(outer) == 4 and len(holes[0]) == 4


def test_inconsistent_winding_still_resolves():
    # flip one quad's winding — orientation propagation must still cancel the shared edge.
    quads = _grid_quads(2, 1)
    quads[1] = quads[1][::-1]
    r = extract_boundary_loops(quads)
    assert r is not None
    outer, holes = r
    assert holes == [] and len(outer) == 4


def test_corner_pinch_returns_none():
    # two quads touching only at a node (degree-4 pinch): not resolvable degree-2 → None.
    q = _grid_quads(1, 1) + [[(1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0)]]
    assert extract_boundary_loops(q) is None
