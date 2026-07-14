"""Native import + evaluation of IFC4x3 alignment reference curves (no OCC).

Regression for the "zero geometry" bug on alignment files: an IfcAlignment (and its segments)
carry only curve representations ('Axis' Curve3D / 'FootPrint' Curve2D / 'Segment'), so the
generic shape importer skipped them and the file rendered empty. The alignment reader now
evaluates the analytic curve — IfcSegmentedReferenceCurve (cant) over an IfcGradientCurve whose
horizontal base is an IfcCompositeCurve of IfcCosineSpiral / IfcLine segments — to a sampled 3D
polyline that renders as GL_LINES.

Ground-truth values are the ifcopenshell.geom (USE_WORLD_COORDS) oracle for
``segmented-reference-curve.ifc`` (buildingSMART IFC4x3 sample); the analytic evaluator matches
them to ~1e-6 (cant z to machine precision).
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.config import Config

FIXTURE = "ifc_files/segmented-reference-curve.ifc"

# Oracle endpoints (ifcopenshell.geom, USE_WORLD_COORDS) for this fixture.
_HORIZONTAL_END = (98.9299, 13.1346)  # cosine-spiral horizontal, s = 100 m
_REF_CURVE_END = (98.9299, 13.1346, -7.92)  # 3D reference curve with cant, s = 100 m
_CANT_Z = {0.0: 0.08, 50.0: -3.92, 100.0: -7.92}  # cant z at base stations


@pytest.fixture(autouse=True)
def _enable_geom():
    Config().update_config_globally("ifc_import_shape_geom", True)


def test_alignment_file_imports_curve_geometry(example_files):
    """The file used to import as 0 physical objects (every product skipped). It now yields the
    IfcAlignment reference curve plus its segments, each a PolyLine."""
    from ada.geom.curves import PolyLine

    a = ada.from_ifc(example_files / FIXTURE)
    objs = list(a.get_all_physical_objects())
    assert len(objs) >= 4, f"expected the alignment + segments, got {len(objs)}"
    polylines = [o for o in objs if o.geom is not None and isinstance(o.geom.geometry, PolyLine)]
    assert len(polylines) >= 4
    assert all(len(o.geom.geometry.points) >= 2 for o in polylines)


def test_cosine_spiral_horizontal_matches_oracle(example_files):
    """IfcCosineSpiral evaluation: the horizontal footprint endpoint and the heading angle at the
    end (which equals the next segment's start bearing, 0.216667 rad)."""
    import ifcopenshell

    from ada.cadit.ifc.read.geom.curves import get_curve
    from ada.cadit.ngeom._alignment_sweep import (
        _cosine_spiral_theta,
        composite_curve_points,
    )

    f = ifcopenshell.open(str(example_files / FIXTURE))
    # #65 is the FootPrint IfcCompositeCurve (cosine-spiral horizontal).
    cc = get_curve(f.by_id(65))
    pts = composite_curve_points(cc, 200)
    assert np.allclose(pts[-1][:2], _HORIZONTAL_END, atol=1e-3)
    assert np.isclose(pts[0][0], 0.0, atol=1e-6) and np.isclose(pts[0][1], 0.0, atol=1e-6)

    spiral = cc.segments[0].parent_curve  # the IfcCosineSpiral
    theta_end = float(_cosine_spiral_theta(spiral, 100.0, np.array([100.0]))[0])
    assert np.isclose(theta_end, 0.2166667, atol=1e-6)


def test_segmented_reference_curve_cant_z(example_files):
    """IfcSegmentedReferenceCurve: (x,y) equals the base gradient curve, and the cant produces the
    exact cosine vertical offset z(s) = e0 + (L^2/CosineTerm)(cos(pi*s/L) - 1)."""
    import ifcopenshell

    from ada.cadit.ifc.read.geom.curves import get_curve
    from ada.cadit.ngeom._alignment_sweep import segmented_reference_curve_points

    f = ifcopenshell.open(str(example_files / FIXTURE))
    src = get_curve(f.by_id(112))  # IfcSegmentedReferenceCurve
    pts = segmented_reference_curve_points(src, 200)  # 201 pts at uniform base station 0..100

    assert np.allclose(pts[0], (0.0, 0.0, 0.08), atol=1e-4)
    assert np.allclose(pts[-1], _REF_CURVE_END, atol=1e-3)
    # cant z at the sampled stations (index = station since n_per == 200 over 100 m)
    for station, z in _CANT_Z.items():
        assert np.isclose(pts[int(station * 2), 2], z, atol=1e-4), f"cant z at s={station}"


def test_sectioned_solid_horizontal_native(example_files):
    """IfcSectionedSolidHorizontal (a profile swept along the alignment directrix over a distance
    range) reads natively as a triangulated swept shell — NOT the OCC-kernel explosion into
    thousands of loose faces — so it renders and STEP-round-trips as one solid. bbox matches the
    ifcopenshell oracle."""
    import ada.geom.surfaces as su

    a = ada.from_ifc(example_files / "ifc_files/sectioned-solid-horizontal.ifc")
    objs = list(a.get_all_physical_objects())
    assert len(objs) == 8  # 1 sectioned solid + 7 alignment curves

    solids = [o for o in objs if o.geom is not None and isinstance(o.geom.geometry, su.TriangulatedFaceSet)]
    assert len(solids) == 1, "the sectioned solid must read as one native triangulated shell"
    tfs = solids[0].geom.geometry
    coords = np.array([[p[0], p[1], p[2]] for p in tfs.coordinates])
    assert len(tfs.indices) % 3 == 0 and len(tfs.indices) // 3 > 100
    # oracle (ifcopenshell.geom, USE_WORLD_COORDS): swept over directrix distance 300..600
    assert np.allclose(coords.min(0), (300.0, -22.26, 148.52), atol=0.1)
    assert np.allclose(coords.max(0), (599.88, 5.0, 149.7), atol=0.1)


def test_alignment_renders_as_lines(example_files):
    """The imported polylines tessellate to GL_LINES (kernel-free discretize_curve path) with the
    expected scene bbox (reference curve dips to z = -7.92 from the cant)."""
    a = ada.from_ifc(example_files / FIXTURE)
    objs = list(a.get_all_physical_objects())

    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.meshes import MeshType

    bt = BatchTessellator()
    seg_total = 0
    mn = np.full(3, 1e18)
    mx = np.full(3, -1e18)
    for ms in bt.batch_tessellate(objs):
        assert ms.type == MeshType.LINES
        p = np.asarray(ms.position, dtype=float).reshape(-1, 3)
        if len(p):
            mn = np.minimum(mn, p.min(0))
            mx = np.maximum(mx, p.max(0))
        seg_total += len(ms.indices) // 2
    assert seg_total > 100
    assert np.isclose(mn[2], -7.92, atol=1e-2)  # cant dip present
    assert mx[0] > 99 and mx[1] > 12  # spans the horizontal footprint
