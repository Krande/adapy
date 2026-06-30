"""Piece 4 of the native alignment-sweep port: IFC4x3 IfcFixedReferenceSweptAreaSolid
(swept over an IfcGradientCurve) -> AP242 STEP must yield exactly ONE solid.

Regression for audit run #48: the file used to produce 15202 loose triangle-faces
(IfcOpenShell OCC kernel fallback) and, after the native reader landed, an EMPTY STEP
(the adacpp B-rep builder has no GradientCurve). The converter now falls back to the
kernel-free streaming writer, which tessellates the analytic sweep (validated NGEOM /
libtess2 path) into one faceted, watertight MANIFOLD_SOLID_BREP. No OCC on the geometry.
"""

from __future__ import annotations

import pathlib

import pytest

import ada  # noqa: F401  (resolves the package root for fixtures)

try:
    import adacpp.cad as _cad
except Exception:  # pragma: no cover - adacpp optional
    _cad = None

pytestmark = pytest.mark.skipif(
    _cad is None or not hasattr(_cad, "tessellate_stream"),
    reason="adacpp.cad.tessellate_stream unavailable (pre-branch / no adacpp)",
)

FIXTURE = "fixed-reference-swept-area-solid.ifc"


def _ifc_fixture_dir() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for up in here.parents:
        cand = up / "files" / "ifc_files"
        if cand.is_dir():
            return cand
    pytest.skip("ifc_files fixtures not found")


def _count_solids(step_path: pathlib.Path) -> int:
    """Solids the way the audit parity gate counts them (adacpp StepNgeomStream),
    falling back to the MANIFOLD_SOLID_BREP text count on older adacpp builds."""
    if hasattr(_cad, "StepNgeomStream"):
        return sum(1 for _ in _cad.StepNgeomStream(str(step_path)))
    return step_path.read_bytes().count(b"MANIFOLD_SOLID_BREP")


def test_alignment_fixed_reference_swept_solid_to_step_is_one_solid(tmp_path):
    from ada.comms.rest.converter import _step_has_solids, _via_ada_to_step

    ifc = _ifc_fixture_dir() / FIXTURE
    if not ifc.is_file():
        pytest.skip(f"{FIXTURE} fixture not present")

    out = pathlib.Path(_via_ada_to_step(ifc, ".ifc", lambda *_: None))
    try:
        assert _step_has_solids(out), "ifc->step produced no solid root (regressed to EMPTY)"
        assert _count_solids(out) == 1, "expected exactly one solid (faceted MANIFOLD_SOLID_BREP)"

        data = out.read_text()
        assert data.count("MANIFOLD_SOLID_BREP") == 1
        assert data.count("CLOSED_SHELL") == 1
        # Watertight closed triangle shell: Euler V - E + F == 2.
        v = data.count("VERTEX_POINT")
        e = data.count("EDGE_CURVE")
        f = data.count("ADVANCED_FACE")
        assert f > 1000, f"expected a tessellated shell, got {f} faces"
        assert v - e + f == 2, f"not a watertight closed shell: V={v} E={e} F={f}"
    finally:
        out.unlink(missing_ok=True)
