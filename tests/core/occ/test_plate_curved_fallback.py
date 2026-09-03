"""A PlateCurved must not vanish because its analytic edge arc is degenerate.

The curved build of a trimmed-spline plate can fail in OCC with
``GC_MakeArcOfCircle::Value() - no result`` when a boundary arc degenerates.
The flat fallback then rebuilds the plate from its footprint — but it carried
the SAME analytic arcs, so it failed identically and the plate was dropped
from the model. On one hull-skin model that silently removed 2162 of 5462
curved plates.

The recovery ladder is therefore: flat with the arcs, flat with the arcs
DROPPED (the first attempt independent of the failure), then the OCC-free
stream kernel for a plate that has no flat footprint at all.
"""

from __future__ import annotations

import pytest

from ada.occ.tessellating import BatchTessellator


class FakePlate:
    """Stands in for a PlateCurved that failed its curved tessellation."""

    def __init__(self, *, pts=((0, 0, 0), (1, 0, 0), (1, 1, 0)), arcs="ARCS", solid=None):
        self._flat_fallback_pts = list(pts) if pts else None
        self._flat_fallback_edge_curves = arcs
        self.name = "plate"
        self.t = 0.01
        self.material = None
        self.parent = None
        self._solid = solid

    def solid_geom(self):
        if self._solid is None:
            raise RuntimeError("no curved geometry")
        return self._solid


@pytest.fixture
def ladder(monkeypatch):
    """Record which edge_curves each fallback attempt was built with."""
    from ada.cadit.gxml.read import helpers

    seen = []

    monkeypatch.setattr(helpers, "_fit_best_fit_plane", lambda pts: None)
    monkeypatch.setattr(helpers, "_project_onto_plane", lambda pts, plane: list(pts))
    monkeypatch.setattr(helpers, "_project_edge_curves_onto_plane", lambda curves, plane: curves)
    monkeypatch.setattr(BatchTessellator, "tessellate_geom", lambda self, *a, **k: "MESH")
    return seen


def _install_plate_from_face(monkeypatch, seen, *, fail_when):
    from ada.cadit.gxml.read import helpers

    def fake(name, pts, edge_curves, t, _n, **kw):
        seen.append(edge_curves)
        if fail_when(edge_curves):
            raise RuntimeError("OCCT StdFail_NotDone: GC_MakeArcOfCircle::Value() - no result")

        class _P:
            def solid_geom(self):
                return "GEOM"

        return _P()

    monkeypatch.setattr(helpers, "_plate_from_face", fake)


def test_a_degenerate_arc_no_longer_takes_the_plate_with_it(monkeypatch, ladder):
    """The arcs fail; retrying without them recovers the plate."""
    _install_plate_from_face(monkeypatch, ladder, fail_when=lambda c: c is not None)
    out = BatchTessellator()._recover_plate_curved(FakePlate(), "n0", None)
    assert out == "MESH", "a plate whose arcs fail must still render flat"
    assert ladder == ["ARCS", None], "the retry must drop the arcs, not repeat them"


def test_a_working_arc_is_kept(monkeypatch, ladder):
    """The arcs are only dropped on failure — they carry real boundary shape."""
    _install_plate_from_face(monkeypatch, ladder, fail_when=lambda c: False)
    out = BatchTessellator()._recover_plate_curved(FakePlate(), "n0", None)
    assert out == "MESH"
    assert ladder == ["ARCS"], "no needless second attempt when the first works"


def test_the_stream_kernel_catches_a_plate_with_no_flat_footprint(monkeypatch, ladder):
    """No footprint to flatten onto: keep the curve via the OCC-free kernel."""
    _install_plate_from_face(monkeypatch, ladder, fail_when=lambda c: True)
    monkeypatch.setattr(
        BatchTessellator,
        "_tessellate_geom_via_stream",
        lambda self, geom, ref, force_pipeline=None, angular_deg=None: "STREAM",
    )
    out = BatchTessellator()._recover_plate_curved(FakePlate(pts=None, solid="SOLID"), "n0", None)
    assert out == "STREAM"
    assert ladder == [], "with no footprint there is nothing to flatten"


def test_a_plate_is_dropped_only_when_every_rung_fails(monkeypatch, ladder):
    _install_plate_from_face(monkeypatch, ladder, fail_when=lambda c: True)
    monkeypatch.setattr(
        BatchTessellator,
        "_tessellate_geom_via_stream",
        lambda self, geom, ref, force_pipeline=None, angular_deg=None: None,
    )
    assert BatchTessellator()._recover_plate_curved(FakePlate(solid="SOLID"), "n0", None) is None
    assert ladder == ["ARCS", None], "both flat attempts must be made before giving up"
