"""Robust boundary extraction (outer + holes, pinches split) from facet loops."""

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


def test_square_one_face_no_holes():
    faces = extract_boundary_loops(_grid_quads(2, 2))
    assert faces is not None and len(faces) == 1
    outer, holes = faces[0]
    assert holes == [] and len(outer) == 4  # collinear boundary points removed to 4 corners


def test_square_with_central_hole():
    faces = extract_boundary_loops(_grid_quads(3, 3, skip={(1, 1)}))
    assert faces is not None and len(faces) == 1
    outer, holes = faces[0]
    assert len(holes) == 1
    assert len(outer) == 4 and len(holes[0]) == 4


def test_inconsistent_winding_still_resolves():
    quads = _grid_quads(2, 1)
    quads[1] = quads[1][::-1]  # flip one quad — orientation propagation must still cancel the shared edge
    faces = extract_boundary_loops(quads)
    assert faces is not None and len(faces) == 1
    outer, holes = faces[0]
    assert holes == [] and len(outer) == 4


def test_corner_pinch_splits_into_two_faces():
    # two quads touching only at a node (degree-4 pinch): angular tracing splits them
    # into two separate material regions rather than one crossed loop.
    q = _grid_quads(1, 1) + [[(1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0)]]
    faces = extract_boundary_loops(q)
    assert faces is not None and len(faces) == 2
    assert all(holes == [] and len(outer) == 4 for outer, holes in faces)


def test_simplify_closed_polygon_collapses_zigzag_to_corners():
    # A rectangle whose edges carry a fine near-collinear zigzag (a merged plate's traced
    # boundary) should collapse to its 4 corners; libtess2 then makes 2 triangles, not ~40.
    from ada.core.vector_utils import simplify_closed_polygon

    pts = []
    n = 20
    for i in range(n):  # bottom edge, +/- tiny zigzag off the straight line
        pts.append((i / n * 10.0, 0.02 * (-1) ** i, 0.0))
    pts.append((10.0, 0.0, 0.0))
    for i in range(n):  # right edge
        pts.append((10.0 + 0.02 * (-1) ** i, i / n * 4.0, 0.0))
    pts.append((10.0, 4.0, 0.0))
    pts.append((0.0, 4.0, 0.0))  # top + left (already straight)
    simp = simplify_closed_polygon(pts, rel_tol=0.03, max_area_change=0.08)
    assert len(simp) <= 6, f"zigzag rectangle should collapse to ~4 corners, got {len(simp)}"


def test_simplify_closed_polygon_guard_keeps_real_corners():
    # An L-shape (a real reflex corner) must NOT be flattened — the area guard reverts any
    # simplification that would drop the notch.
    from ada.core.vector_utils import simplify_closed_polygon

    L = [
        (0, 0, 0),
        (4, 0, 0),
        (4, 2, 0),
        (2, 2, 0),
        (2, 4, 0),
        (0, 4, 0),
    ]
    simp = simplify_closed_polygon(L, rel_tol=0.03, max_area_change=0.08)
    assert len(simp) == 6, "the L-shape's reflex corner must be preserved"
