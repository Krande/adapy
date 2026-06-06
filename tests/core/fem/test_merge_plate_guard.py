"""Guard against degenerate / self-intersecting merged coplanar plates.

The edge-cancellation merge only guarantees a topologically single, degree-2
boundary. A geometrically self-intersecting result still slips through and later
crashes the STEP export ("Segments do not form a closed loop"). The guard
validates the merged 2D loop and falls back to the unmerged plates.
"""

import ada
from ada import Plate
from ada.fem.formats import concept_merge as cm
from ada.fem.formats.concept_merge import _loop_is_simple_2d, merge_coplanar_plates


def test_loop_is_simple_2d_accepts_valid_polygons():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    concave_l = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    assert _loop_is_simple_2d(square)
    assert _loop_is_simple_2d(concave_l)


def test_loop_is_simple_2d_rejects_self_intersection():
    bowtie = [(0, 0), (1, 1), (1, 0), (0, 1)]  # classic figure-8
    assert not _loop_is_simple_2d(bowtie)


def test_loop_is_simple_2d_rejects_degenerate():
    sliver = [(0, 0), (1, 0), (2, 0)]  # collinear -> zero area
    too_few = [(0, 0), (1, 0)]
    assert not _loop_is_simple_2d(sliver)
    assert not _loop_is_simple_2d(too_few)


def test_valid_adjacent_plates_still_merge():
    """Two edge-adjacent coplanar plates of matching material/thickness merge."""
    a = Plate.from_3d_points("a", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01)
    b = Plate.from_3d_points("b", [(1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)], 0.01)
    parent = ada.Part("p")
    out = merge_coplanar_plates([a, b], parent)
    assert len(out) == 1
    assert out[0].name.endswith("_m")


def test_self_intersecting_merge_falls_back(monkeypatch):
    """A self-intersecting merged loop must fall back to the original plates."""
    a = Plate.from_3d_points("a", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.01)
    b = Plate.from_3d_points("b", [(1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)], 0.01)
    parent = ada.Part("p")

    # Force the edge-cancellation step to return a bowtie (planar, z=0).
    # _merge_coplanar_component imports the symbol from vector_utils at call time.
    import ada.core.vector_utils as vu

    bowtie3d = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    monkeypatch.setattr(vu, "merge_coplanar_loops_by_edge_cancellation", lambda loops, ndigits=6: bowtie3d)

    out = cm._merge_coplanar_component([a, b], parent, ndigits=6)
    assert out == [a, b]  # fell back to originals, did not emit a corrupt _m plate
