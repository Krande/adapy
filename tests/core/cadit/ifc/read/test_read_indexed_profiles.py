"""Native reading of two profile/curve cases that previously rendered wrong geometry.

1. IfcRoundedRectangleProfileDef — a subtype of IfcRectangleProfileDef, so the plain-rectangle
   branch used to swallow it and drop RoundingRadius, giving SHARP corners (bath-csg void).
2. IfcIndexedPolyCurve with a multi-point IfcLineIndex — a polyline through N points is N-1 edges,
   but only the first two points were kept, collapsing e.g. an I-section's flange outline.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.config import Config


@pytest.fixture(autouse=True)
def _enable_geom():
    Config().update_config_globally("ifc_import_shape_geom", True)


def _render_bbox(a):
    from ada.occ.tessellating import BatchTessellator

    objs = list(a.get_all_physical_objects())
    p = np.vstack([np.asarray(m.position, float).reshape(-1, 3) for o in objs for m in BatchTessellator().batch_tessellate([o])])
    return p.min(0), p.max(0)


def test_rounded_rectangle_profile_rounds_corners(example_files):
    """bath-csg-solid: the IfcRoundedRectangleProfileDef void reads as an ArbitraryProfileDef with 4
    corner arcs (not a sharp rectangle), so the boolean cavity has rounded interior corners."""
    import ada.geom.curves as cu
    import ada.geom.surfaces as su

    a = ada.from_ifc(example_files / "ifc_files/bath-csg-solid.ifc")
    o = list(a.get_all_physical_objects())[0]
    bops = o.geom.bool_operations or []
    assert bops, "expected the DIFFERENCE boolean (block - void)"
    void_profile = bops[0].second_operand.geometry.swept_area
    assert isinstance(void_profile, su.ArbitraryProfileDef)  # not a sharp RectangleProfileDef
    arcs = sum(1 for s in void_profile.outer_curve.segments if isinstance(s, cu.ArcLine))
    assert arcs == 4  # one fillet per corner


def test_indexed_polycurve_multipoint_line_index(example_files):
    """beam-extruded-solid: the I-section profile (IfcIndexedPolyCurve with multi-point IfcLineIndex
    runs) expands to consecutive edges instead of collapsing to a single edge per run — so the full
    IPE200 (100mm wide x 200mm tall) is built, not a ~half-width box."""
    src = example_files / "ifc_files/beam-extruded-solid.ifc"
    a = ada.from_ifc(src)
    o = list(a.get_all_physical_objects())[0]
    # Deterministic, backend-free: the 4 lines + 4 arcs, with the two 6-point line runs each
    # expanded to 5 edges, give 16 segments (was 8 when runs collapsed to one edge).
    assert len(o.geom.geometry.swept_area.outer_curve.segments) == 16
    # End-to-end: the rendered profile spans the full width/height (the collapse gave x-width ~0.047
    # instead of ~0.1). Coarse thresholds so adacpp tessellation jitter doesn't flake the test.
    rmn, rmx = _render_bbox(a)
    size = rmx - rmn
    assert size[0] > 0.09, size  # full 100mm flange width, not the collapsed ~47mm
    assert size[2] > 0.18, size  # full 200mm section height
    assert abs(size[1] - 1.0) < 0.02, size  # 1 m extrusion length
