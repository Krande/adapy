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


def _sweep_tris(ifc_path: pathlib.Path) -> int:
    """Triangles the *available* adacpp build produces for the fixture's swept solid.
    0 means this build lacks SweepN / FIXED_REF_SWEPT_SOLID (tag 54) decoding — i.e.
    the conda ``ada-cpp`` 0.11.0 the CI ``tests-adacpp`` env pins, which predates
    ``dfde659``. The bleeding-edge overlay (``tests-adacpp-dev`` / the deployed
    ``ADACPP_BRANCH`` overlay) and any future release that ships the verb return >0.
    Lets the test distinguish 'build can't sweep' (skip) from 'sweep tessellates but
    STEP emit is empty' (real regression -> fail)."""
    from ada.cad import active_backend
    from ada.comms.rest import converter as conv

    try:
        model = conv._load_with_ada(pathlib.Path(ifc_path), ".ifc")
        backend = active_backend()
        if getattr(backend, "tessellate_stream", None) is None:
            return 0
        for obj in model.get_all_physical_objects():
            sg = getattr(obj, "solid_geom", None)
            if sg is None:
                continue
            try:
                g = sg().geometry
                mesh = backend.tessellate_stream([("0", g)], pipeline="libtess2", deflection=2.0, angular_deg=20.0)
            except Exception:  # noqa: BLE001
                continue
            n = len(mesh.indices) if mesh.indices is not None else 0
            if n:
                return n
    except Exception:  # noqa: BLE001 - capability probe must never error the test
        return 0
    return 0


def test_alignment_fixed_reference_swept_solid_to_step_is_one_solid(tmp_path):
    from ada.comms.rest.converter import _step_has_solids, _via_ada_to_step

    ifc = _ifc_fixture_dir() / FIXTURE
    if not ifc.is_file():
        pytest.skip(f"{FIXTURE} fixture not present")

    out = pathlib.Path(_via_ada_to_step(ifc, ".ifc", lambda *_: None))
    try:
        if not _step_has_solids(out):
            # No solid emitted. If this adacpp build can't even tessellate the sweep
            # (conda ada-cpp <= 0.11.0, pre-dfde659), skip — the SweepN verb isn't
            # present. If it CAN tessellate but the STEP is still empty, that's a real
            # emit regression.
            if _sweep_tris(ifc) == 0:
                pytest.skip("adacpp build lacks FIXED_REF_SWEPT_SOLID/SweepN support (needs dfde659+ / ada-cpp release)")
            pytest.fail("ifc->step produced no solid root despite a tessellable sweep (emit regression)")
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
