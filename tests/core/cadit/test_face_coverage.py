"""Face-level tessellation coverage metric (ada.cadit.diagnostics.face_coverage).

Counts, per surface type, how many of a solid's faces survive BUILD and MESH on the
active CAD backend. The gaps (dropped at build, unmeshed) are the residual a
coverage-complete kernel must recover; this is the baseline metric + regression gate.
"""

from __future__ import annotations

import pytest

import ada
from ada.cadit.diagnostics import FaceCoverage, face_coverage


def test_face_coverage_box_is_complete():
    cov = face_coverage(ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)).solid_geom())
    assert cov.total == 6 and cov.built == 6 and cov.meshed == 6
    assert cov.pct == 100.0
    assert cov.by_type["plane"] == [6, 6, 6]  # [meshed, built, total]


def test_face_coverage_cylinder_has_curved_face():
    cov = face_coverage(ada.PrimCyl("cy", (0, 0, 0), (0, 0, 1), 0.4).solid_geom())
    assert cov.meshed == cov.total == 3  # 2 planar caps + 1 cylindrical side
    assert cov.by_type["cylinder"][0] == 1  # the seam face meshed
    assert cov.pct == 100.0


def test_face_coverage_brep_stream_uses_geom_face_total(tmp_path):
    # A box round-tripped through STEP yields a B-rep ClosedShell with an explicit
    # cfs_faces list — face_coverage takes its total from that list.
    from ada.cadit.step.read.stream_reader import stream_read_step

    out = tmp_path / "box.step"
    (ada.Assembly("m") / (ada.Part("p") / ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)))).to_stp(out)

    agg = FaceCoverage()
    for g in stream_read_step(out, local_pool=False, tolerant=True):
        agg.add(face_coverage(g))
    assert agg.total == 6 and agg.meshed == 6
    assert agg.by_type["plane"] == [6, 6, 6]


def test_facecoverage_add_and_pct():
    a = FaceCoverage(total=10, built=8, meshed=7, by_type={"plane": [7, 8, 10]})
    b = FaceCoverage(total=2, built=2, meshed=2, by_type={"cone": [2, 2, 2]})
    a.add(b)
    assert a.total == 12 and a.built == 10 and a.meshed == 9
    assert a.pct == pytest.approx(75.0)
    assert a.by_type["cone"] == [2, 2, 2]
