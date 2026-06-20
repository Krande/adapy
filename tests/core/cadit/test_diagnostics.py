"""ada.cadit.diagnostics — geometry health + GLB-vs-GLB comparison.

The comparison must be FAIR: both sides are welded and measured the same way,
and parts are matched by location (not name — adapy's stream reader emits
solid_<n> while step2glb keeps STEP product names). A GLB compared to itself
must therefore report zero divergence; a model missing a solid must surface it.
"""

import numpy as np
import pytest

import ada
from ada import Beam, Part, Section
from ada.cadit.diagnostics import (
    compare_glb_geometry,
    diagnose_object,
    glb_parts,
    mesh_health,
)
from ada.cadit.step.stream_to_glb import stream_step_to_glb


def test_mesh_health_clean_quad_is_watertight_free_of_degeneracy():
    # two triangles forming a unit square — no degenerate/sliver/non-manifold
    pos = np.array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], dtype="float32")
    idx = np.array([0, 1, 2, 0, 2, 3], dtype="uint32")
    h = mesh_health(pos, idx)
    assert h["n_tris"] == 2
    assert h["degenerate_tris"] == 0
    assert h["area"] == pytest.approx(1.0, rel=1e-5)
    assert h["nonmanifold_edges"] == 0


def test_mesh_health_flags_zero_area_triangle():
    # a triangle with three collinear points has zero area
    pos = np.array([0, 0, 0, 1, 0, 0, 2, 0, 0], dtype="float32")
    idx = np.array([0, 1, 2], dtype="uint32")
    h = mesh_health(pos, idx)
    assert h["degenerate_tris"] == 1


def test_diagnose_object_primbox_is_healthy():
    d = diagnose_object(ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)))
    assert d.ok, d.summary()
    t = d.stage("tessellate")
    assert t.metrics["watertight"] is True
    assert t.metrics["n_tris"] >= 12


def _stream_glb(tmp_path, objs, name="m"):
    src = tmp_path / f"{name}.step"
    (ada.Assembly(name) / (Part("p") / objs)).to_stp(src)
    glb = tmp_path / f"{name}.glb"
    stream_step_to_glb(src, glb, tolerant=True)
    return glb


def test_glb_parts_extracts_one_record_per_solid(tmp_path):
    glb = _stream_glb(tmp_path, [
        Beam("b1", (0, 0, 0), (3, 0, 0), Section("IPE300", from_str="IPE300")),
        Beam("b2", (0, 2, 0), (3, 2, 0), Section("IPE300", from_str="IPE300")),
    ])
    parts = glb_parts(glb)
    assert len(parts) == 2
    assert all(p.area > 0 and p.n_tris > 0 for p in parts)


def test_compare_self_is_zero_divergence(tmp_path):
    # Fairness guard: a GLB vs itself must match every part with no divergence.
    glb = _stream_glb(tmp_path, [
        ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)),
        ada.PrimCyl("cy", (3, 0, 0), (3, 0, 1), 0.4),
    ])
    cmp = compare_glb_geometry(glb, glb)
    assert cmp.matched == cmp.parts_a == cmp.parts_b == 2
    assert cmp.only_in_a == [] and cmp.only_in_b == []
    assert cmp.diverged == []
    assert cmp.totals["area_ratio"] == pytest.approx(1.0, rel=1e-6)


def test_compare_detects_missing_part(tmp_path):
    # B has a solid A lacks (same coordinates) -> reported as MISSING in A,
    # matched purely by location even though names are solid_<n> on both sides.
    full = _stream_glb(tmp_path, [
        ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)),
        ada.PrimBox("by", (5, 0, 0), (6, 1, 1)),
    ], name="full")
    partial = _stream_glb(tmp_path, [
        ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)),
    ], name="partial")
    cmp = compare_glb_geometry(partial, full)  # A=partial, B=full
    assert cmp.matched == 1
    assert len(cmp.only_in_b) == 1  # the second box, present only in B
    assert cmp.only_in_a == []
