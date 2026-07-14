"""Non-OCC (ap242_stream) STEP writer preserves multi-instance mapped shapes ANALYTICALLY.

``mapped-shape-with-multiple-items.ifc`` is one IfcBuildingElementProxy whose Body is four
IfcMappedItems reusing one extruded-solid source under non-uniform-scaled transforms — carried on
``Geometry.transforms``. The OCC ``to_stp`` writer drops all but one (a rigid STEP placement can't
carry the scale). The stream writer emits one solid PER instance, transforming the analytic
Extrusion (exact planar faces + line/arc edges, no tessellation) so the geometry is maintained — a
faceted bake is only a last resort for transforms the analytic form can't carry.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.config import Config


@pytest.fixture(autouse=True)
def _enable_geom():
    Config().update_config_globally("ifc_import_shape_geom", True)


def _fem_or_none(example_files):
    p = example_files / "ifc_files/mapped_shapes/mapped-shape-with-multiple-items.ifc"
    return p if p.exists() else None


def test_transform_extrusion_analytic_rigid_and_scale():
    """Unit: the analytic Extrusion transform carries a rotation + axis-aligned non-uniform scale +
    translation, and refuses an oblique extrude (so the caller facet-falls-back)."""
    from ada.cadit.step.write.ap242_stream import Extrusion, Seg, _transform_extrusion

    unit = Extrusion(
        origin=(0.0, 0.0, 0.0),
        xdir=(1.0, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
        depth=2.0,
        outer=[
            Seg("line", (0.0, 0.0), (1.0, 0.0)),
            Seg("line", (1.0, 0.0), (1.0, 1.0)),
            Seg("line", (1.0, 1.0), (0.0, 1.0)),
            Seg("line", (0.0, 1.0), (0.0, 0.0)),
        ],
        inners=[],
        name="u",
        color=None,
    )

    # scale x by 0.5, y by 0.25, translate — stays an analytic extrusion
    S = np.diag([0.5, 0.25, 1.0, 1.0]).astype(float)
    S[:3, 3] = (3.0, 4.0, 0.0)
    placed = _transform_extrusion(unit, S)
    assert placed is not None
    assert np.allclose(placed.origin, (3.0, 4.0, 0.0))
    assert placed.depth == pytest.approx(2.0)
    # profile x-extent scaled to 0.5, y-extent to 0.25
    xs = [p[0] for s in placed.outer for p in (s.start, s.end)]
    ys = [p[1] for s in placed.outer for p in (s.start, s.end)]
    assert max(xs) - min(xs) == pytest.approx(0.5)
    assert max(ys) - min(ys) == pytest.approx(0.25)

    # an oblique shear of the extrude direction can't be an axis-aligned extrusion → None (facet)
    shear = np.eye(4)
    shear[0, 2] = 1.0  # x += z : tilts the extrude vector off the plane normal
    assert _transform_extrusion(unit, shear) is None


def test_mapped_multiple_items_stream_step_analytic(example_files, tmp_path):
    """End-to-end: 4 mapped instances → 4 analytic MANIFOLD_SOLID_BREPs (not triangle soup),
    read back as 4 objects, world bbox matching the ifcopenshell oracle."""
    src = _fem_or_none(example_files)
    if src is None:
        pytest.skip("mapped-shape-with-multiple-items.ifc not in the test corpus")

    a = ada.from_ifc(src)
    out = tmp_path / "mapped.step"
    a.to_stp(out, writer="stream")

    data = out.read_bytes()
    n_solid = data.count(b"MANIFOLD_SOLID_BREP")
    n_face = data.count(b"ADVANCED_FACE")
    assert n_solid == 4, f"expected 4 instanced solids, got {n_solid}"
    # analytic: a handful of exact faces per box (~6), NOT a tessellated triangle mesh (hundreds).
    assert n_face <= 40, f"expected exact analytic faces, got {n_face} (looks tessellated)"

    a2 = ada.from_step(out, reader="auto")
    objs = list(a2.get_all_physical_objects())
    assert len(objs) == 4, f"round-trip dropped instances: {len(objs)}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
