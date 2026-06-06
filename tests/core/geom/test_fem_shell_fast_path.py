"""Fast-path FEM-shell Plate constructor (``from_fem_shell``).

The fast path skips ``build_polycurve``/``SegCreator`` and the computed-placement
LRU (which thrashes on per-element placements). These tests assert it is
geometrically identical to the general ``from_3d_points`` path for flat shells,
and that it really does bypass the hot spots.
"""

import numpy as np
import pytest

from ada import Plate
from ada.api.curves import CurvePoly2d

# tri, axis-aligned quad, tilted (non-axis-aligned) quad, offset tri, and a
# reversed-winding quad -- exercises all the orientation / winding branches.
CASES = {
    "quad": [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)],
    "tri": [(0, 0, 0), (3, 0, 0), (0, 2, 0)],
    "tilted_quad": [(0, 0, 0), (2, 0, 1), (2, 1, 1.5), (0, 1, 0.5)],
    "offset_tri": [(5, 5, 5), (8, 5, 5), (5, 9, 5)],
    "ccw_quad": [(0, 0, 0), (0, 1, 0), (2, 1, 0), (2, 0, 0)],
}


@pytest.mark.parametrize("name", list(CASES))
def test_from_fem_shell_matches_from_3d_points(name):
    pts = CASES[name]
    a = CurvePoly2d.from_3d_points(pts)
    b = CurvePoly2d.from_fem_shell(pts)

    for attr in ("origin", "xdir", "ydir", "normal"):
        assert np.allclose(np.asarray(getattr(a, attr)), np.asarray(getattr(b, attr)), atol=1e-9), attr

    assert np.allclose(np.asarray(a.points2d), np.asarray(b.points2d), atol=1e-9)
    assert np.allclose(np.asarray(a.points3d), np.asarray(b.points3d), atol=1e-9)

    # The polyline that drives IFC/STEP export: indexed point list + index map.
    assert np.allclose(np.asarray(a.seg_global_points), np.asarray(b.seg_global_points), atol=1e-9)
    assert a.seg_index == b.seg_index

    # 2D segments feed solid_geom's outer curve -- order + endpoints must match.
    a_segs = [(np.asarray(s.p1), np.asarray(s.p2)) for s in a.segments]
    b_segs = [(np.asarray(s.p1), np.asarray(s.p2)) for s in b.segments]
    assert len(a_segs) == len(b_segs)
    for (a1, a2), (b1, b2) in zip(a_segs, b_segs):
        assert np.allclose(a1, b1, atol=1e-9) and np.allclose(a2, b2, atol=1e-9)


@pytest.mark.parametrize("name", list(CASES))
def test_solid_geom_parity(name):
    pts = CASES[name]
    fast = Plate.from_fem_shell("p", pts, 0.02)
    gen = Plate.from_3d_points("p", pts, 0.02)

    gf = fast.solid_geom().geometry
    gg = gen.solid_geom().geometry
    assert type(gf).__name__ == type(gg).__name__ == "ExtrudedAreaSolid"
    assert gf.depth == gg.depth
    assert np.allclose(np.asarray(gf.position.location), np.asarray(gg.position.location), atol=1e-9)
    assert np.allclose(np.asarray(gf.position.axis), np.asarray(gg.position.axis), atol=1e-9)


def test_from_fem_shell_bypasses_lru_and_segcreator(monkeypatch):
    """The fast path must not touch get_computed_placement_cached or SegCreator."""
    import ada.api.computed_placement as cp
    import ada.core.curve_utils as cu

    counts = {"lru": 0, "segcreator": 0}

    orig_cached = cp.get_computed_placement_cached

    def counting_cached(*a, **k):
        counts["lru"] += 1
        return orig_cached(*a, **k)

    # create_computed_placement_from_placement calls the module-level symbol
    monkeypatch.setattr(cp, "get_computed_placement_cached", counting_cached)

    orig_segc = cu.SegCreator

    class CountingSegCreator(orig_segc):
        def __init__(self, *a, **k):
            counts["segcreator"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(cu, "SegCreator", CountingSegCreator)

    pts = CASES["tilted_quad"]
    for _ in range(50):
        CurvePoly2d.from_fem_shell(pts)
    assert counts["lru"] == 0
    assert counts["segcreator"] == 0

    # Sanity: the general path DOES hit both (guards against a no-op monkeypatch).
    counts.update(lru=0, segcreator=0)
    for _ in range(50):
        CurvePoly2d.from_3d_points(pts)
    assert counts["lru"] > 0
    assert counts["segcreator"] > 0


def test_fem_conversion_uses_fast_path():
    """End-to-end: a meshed plate converted from FEM yields valid plates."""
    import ada

    pl = Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(0.25, "shell")
    a = ada.Assembly("a") / p
    a.create_objects_from_fem()

    plates = list(p.get_all_physical_objects(by_type=Plate))
    assert len(plates) > 0
    # every converted shell plate builds a valid extruded solid
    for cpl in plates:
        g = cpl.solid_geom().geometry
        assert type(g).__name__ == "ExtrudedAreaSolid"
