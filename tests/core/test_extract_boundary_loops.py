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
