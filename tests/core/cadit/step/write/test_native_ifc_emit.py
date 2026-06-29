"""Validate the native adacpp IFC4 B-rep emitter (adacpp.cad.step_emit_ifc_brep, Phase 1 of the
native streaming STEP->IFC writer) with ifcopenshell's VALIDATION TOOL — schema + WHERE rules,
not tessellation. The emitter is dep-free C++; ifcopenshell is only the test oracle here.

Targets IFC4X3_ADD2 (primary) and IFC4 — the B-rep/geometry-resource entities are identical in
both. Skips cleanly when adacpp lacks the verb (conda build predating the branch) or ifcopenshell
isn't installed."""
from __future__ import annotations

import re

import pytest

import ada  # noqa: F401  (ensures the test env resolves the package root for fixtures)

ifcopenshell = pytest.importorskip("ifcopenshell")
pytest.importorskip("ifcopenshell.validate")
import ifcopenshell.guid  # noqa: E402
import ifcopenshell.validate  # noqa: E402

try:
    import adacpp.cad as _cad
except Exception:  # pragma: no cover - adacpp optional
    _cad = None

pytestmark = pytest.mark.skipif(
    _cad is None or not hasattr(_cad, "step_emit_ifc_brep"),
    reason="adacpp.cad.step_emit_ifc_brep unavailable (pre-branch conda build)",
)

# Exercise the full surface/curve coverage: planar, B-spline surface+curve, circle, and (Ventilator)
# cylinder/cone/torus/surface-of-revolution/linear-extrusion. "No geometry left behind" — every face
# must emit and the file must validate.
FIXTURES = [
    "flat_plate_abaqus_10x10_m.stp",
    "curved_plate.stp",
    "bsplinesurfacewithknots.stp",
    "plate_3_curved.stp",
    "plate_2_curved_complex.stp",
    "Ventilator.stp",  # cylinder + cone (->IfcSurfaceOfRevolution) + torus + extrusion + bspline
]
SCHEMAS = ["IFC4X3_ADD2", "IFC4"]


def _fixture_dir() -> str:
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for up in here.parents:
        cand = up / "files" / "step_files"
        if cand.is_dir():
            return str(cand) + "/"
    pytest.skip("step_files fixtures not found")


def _ifc_fixture_dir() -> str:
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for up in here.parents:
        cand = up / "files" / "ifc_files"
        if cand.is_dir():
            return str(cand) + "/"
    pytest.skip("ifc_files fixtures not found")


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_ifc_to_step"), reason="no stream_ifc_to_step")
@pytest.mark.parametrize(
    "fixture,exp_min,exp_max,n_solids",
    [
        # mm; values cross-checked against OCC (ada.from_ifc) bbox, rel-err 0.0. Rectangle profiles +
        # scaled/rotated mapped instances + IfcLocalPlacement -> native EXTRUDED_AREA_SOLID / baked B-rep.
        # (fixture, expected mesh bbox in METRES, expected solids_out). mapped: mm; beams: m / mm.
        # All cross-checked at rel-err 0.0 vs ifcopenshell.geom (use-world-coords).
        ("mapped_shapes/mapped-shape-with-transformation.ifc", (0.64645, -0.35355, 0), (1.35355, 0.35355, 2), 1),
        ("mapped_shapes/mapped-shape-with-multiple-items.ifc", (0.64645, -0.35355, 0), (2.35355, 1.35355, 2), 1),
        ("beams/beam-extruded-solid.ifc", (-0.11, 0, -0.3), (0.11, 10, 0.3), 1),  # IfcIShapeProfileDef
        ("beams/beam-standard-case.ifc", (-0.03, -0.11, -0.22), (2.97, 12.38, 2.241), 18),  # I + T profiles
        ("beams/beam-varying-cardinal-points.ifc", (-0.05, 0, -0.2), (0.6, 1.0, 0.2), 4),
    ],
)
def test_native_ifc_extrusion_to_step(fixture, exp_min, exp_max, n_solids, tmp_path):
    """Native IFC->STEP covers IfcExtrudedAreaSolid with rectangle + parametric (I/T-shape) profiles,
    mapped instances (scale/rotation), and the product ObjectPlacement — fully native
    (products_skipped==0), re-tessellates to the ifcopenshell.geom-validated bbox (rel-err 0.0)."""
    import numpy as np

    src = _ifc_fixture_dir() + fixture
    out = str(tmp_path / "ex.step")
    st = _cad.stream_ifc_to_step(src, out, 2.0, 20.0, 0)
    assert st["products_skipped"] == 0 and st["solids_out"] == n_solids
    m = _cad.stream_step_to_meshes(out, "libtess2", 2.0, 20.0)
    a = np.asarray(m.positions).reshape(-1, 3) * st["unit_scale"]  # native unit -> metres
    assert len(a) > 0
    assert np.allclose(a.min(0), exp_min, atol=0.01), a.min(0)
    assert np.allclose(a.max(0), exp_max, atol=0.01), a.max(0)


def _wrap(brep_lines: str, brep_id: str, schema: str) -> str:
    g = ifcopenshell.guid.new
    pre = (
        "#1=IFCCARTESIANPOINT((0.,0.,0.));\n#2=IFCDIRECTION((0.,0.,1.));\n#3=IFCDIRECTION((1.,0.,0.));\n"
        "#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);\n#5=IFCDIRECTION((1.,0.));\n"
        "#6=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-5,#4,#5);\n"
        "#7=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);\n#8=IFCUNITASSIGNMENT((#7));\n"
        f"#9=IFCPROJECT('{g()}',$,'P',$,$,$,$,(#6),#8);\n"
        "#10=IFCAXIS2PLACEMENT3D(#1,$,$);\n#11=IFCLOCALPLACEMENT($,#10);\n"
        f"#12=IFCSITE('{g()}',$,'Site',$,$,#11,$,$,.ELEMENT.,$,$,$,$,$);\n"
        f"#13=IFCRELAGGREGATES('{g()}',$,$,$,#9,(#12));\n"
    )
    body = pre + brep_lines + (
        f"#900=IFCSHAPEREPRESENTATION(#6,'Body','AdvancedBrep',(#{brep_id}));\n"
        f"#901=IFCPRODUCTDEFINITIONSHAPE($,$,(#900));\n"
        f"#902=IFCBUILDINGELEMENTPROXY('{g()}',$,'proxy',$,$,#11,#901,$,$);\n"
        f"#903=IFCRELCONTAINEDINSPATIALSTRUCTURE('{g()}',$,$,$,(#902),#12);\n"
    )
    return (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('t','',(''),(''),'','','');\n"
        f"FILE_SCHEMA(('{schema}'));\nENDSEC;\nDATA;\n{body}ENDSEC;\nEND-ISO-10303-21;\n"
    )


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_step_to_ifc"), reason="no stream_step_to_ifc")
@pytest.mark.parametrize("fixture", ["Ventilator.stp", "curved_plate.stp", "plate_3_curved.stp"])
def test_stream_step_to_ifc_file_lossless_and_valid(fixture, tmp_path):
    """Full-file writer (Phase 2): every solid + every face survives STEP->ng::->IFC (no geometry
    left behind), and the file validates against IFC4X3_ADD2."""
    out = str(tmp_path / (fixture + ".ifc"))
    st = _cad.stream_step_to_ifc(_fixture_dir() + fixture, out, "IFC4X3_ADD2", 2.0, 20.0)
    assert st["solids_in"] > 0 and st["solids_out"] == st["solids_in"], f"solid dropped: {st}"
    assert st["faces_out"] == st["faces_in"] and st["faces_dropped"] == 0, f"face dropped: {st}"
    assert st["edges_degenerate"] == 0, f"degenerate edge: {st}"
    f = ifcopenshell.open(out)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger)
    issues = [str(s.get("message", s)) for s in logger.statements]
    assert not issues, f"{fixture} ifcopenshell.validate issues: {issues[:5]}"
    assert len(f.by_type("IfcAdvancedBrep")) == st["solids_out"]


def _mesh_bbox(path):
    import numpy as np

    m = _cad.stream_step_to_meshes(path, "libtess2", 2.0, 20.0)
    a = np.asarray(m.positions).reshape(-1, 3)
    return (a.min(0), a.max(0)) if len(a) else (None, None)


def _bbox_relerr(a_path, b_path):
    import numpy as np

    amn, amx = _mesh_bbox(a_path)
    bmn, bmx = _mesh_bbox(b_path)
    if amn is None or bmn is None:
        return 1.0
    diag = float(np.linalg.norm(amx - amn)) or 1.0
    return float(np.linalg.norm((bmn + bmx) / 2 - (amn + amx) / 2) + np.linalg.norm((bmx - bmn) - (amx - amn))) / diag


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_step_to_step"), reason="no stream_step_to_step")
@pytest.mark.parametrize("fixture", ["Ventilator.stp", "plate_2_curved_complex.stp", "curved_plate.stp"])
def test_native_step_to_step_roundtrip(fixture, tmp_path):
    """Native STEP->STEP (AP242): re-export is lossless + the emitted STEP re-tessellates to the same
    per-model bbox as the source."""
    pytest.importorskip("numpy")
    src = _fixture_dir() + fixture
    out = str(tmp_path / "rt.stp")
    st = _cad.stream_step_to_step(src, out, 2.0, 20.0, 0, 0)
    assert st["solids_out"] == st["solids_in"] > 0 and st["faces_dropped"] == 0
    assert _bbox_relerr(src, out) < 0.02


@pytest.mark.skipif(
    not (hasattr(_cad or object(), "stream_ifc_to_step") and hasattr(_cad or object(), "stream_step_to_ifc")),
    reason="no native ifc<->step",
)
@pytest.mark.parametrize("fixture", ["Ventilator.stp", "plate_3_curved.stp", "curved_plate.stp"])
def test_native_step_ifc_step_roundtrip(fixture, tmp_path):
    """Full circle: STEP -> (native STEP->IFC) -> IFC -> (native IFC->STEP) -> STEP; the final mesh
    bbox matches the source (geometry survives both native conversions), units preserved."""
    pytest.importorskip("numpy")
    src = _fixture_dir() + fixture
    ifc = str(tmp_path / "rt.ifc")
    stp = str(tmp_path / "rt.stp")
    _cad.stream_step_to_ifc(src, ifc, "IFC4X3_ADD2", 2.0, 20.0, 0, 0)
    st = _cad.stream_ifc_to_step(ifc, stp, 2.0, 20.0, 0)
    assert st["solids_out"] > 0 and st["faces_dropped"] == 0
    assert _bbox_relerr(src, stp) < 0.02


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_step_to_ifc"), reason="no stream_step_to_ifc")
def test_adapy_native_step_to_ifc_wrapper(tmp_path):
    """Phase 4: the adapy wrapper ada.cadit.step.native_step_to_ifc (what the converter calls) prefers
    the native writer, is lossless, and validates."""
    from ada.cadit.step.native_step_to_ifc import native_ifc_available, native_step_to_ifc

    assert native_ifc_available()
    out = str(tmp_path / "wrap.ifc")
    stats = native_step_to_ifc(_fixture_dir() + "Ventilator.stp", out)
    assert stats["solids_out"] == stats["solids_in"] and stats["faces_dropped"] == 0
    f = ifcopenshell.open(out)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger)
    assert not logger.statements, [str(s.get("message")) for s in logger.statements[:3]]


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_step_to_ifc"), reason="no stream_step_to_ifc")
@pytest.mark.parametrize("fixture", ["Ventilator.stp", "plate_3_curved.stp"])
def test_stream_step_to_ifc_parallel_matches_serial(fixture, tmp_path):
    """Phase 3: the parallel writer (num_threads=4, disjoint id blocks) is lossless, has no id-block
    overflow, and validates — same as serial (num_threads=1)."""
    src = _fixture_dir() + fixture
    par = str(tmp_path / "par.ifc")
    ser = str(tmp_path / "ser.ifc")
    sp = _cad.stream_step_to_ifc(src, par, "IFC4X3_ADD2", 2.0, 20.0, 0, 4)
    ss = _cad.stream_step_to_ifc(src, ser, "IFC4X3_ADD2", 2.0, 20.0, 0, 1)
    assert sp["solids_out"] == ss["solids_out"] and sp["faces_out"] == ss["faces_out"]
    assert sp["faces_dropped"] == 0 and sp["drop_reasons"].get("id_block_overflow", 0) == 0
    f = ifcopenshell.open(par)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger)
    assert not logger.statements, f"{fixture} parallel validate: {[str(s.get('message')) for s in logger.statements[:3]]}"


@pytest.mark.skipif(not hasattr(_cad or object(), "stream_step_to_ifc"), reason="no stream_step_to_ifc")
@pytest.mark.parametrize("fixture", ["curved_plate.stp", "plate_3_curved.stp", "bsplinesurfacewithknots.stp"])
def test_ifc_geometry_matches_glb_oracle(fixture, tmp_path):
    """GLB-oracle parity: the IFC brep's CartesianPoints must span the same per-solid bbox as the
    STEP->GLB tessellation oracle (the IFC carries the same ng:: vertices the GLB tessellates)."""
    np = pytest.importorskip("numpy")
    src = _fixture_dir() + fixture
    mesh = _cad.stream_step_to_meshes(src, "libtess2", 2.0, 20.0)
    pos = np.asarray(mesh.positions).reshape(-1, 3)
    oracle = {g.node_id: (pos[g.vstart:g.vstart + g.vlength].min(0), pos[g.vstart:g.vstart + g.vlength].max(0))
              for g in mesh.groups if g.vlength}
    out = str(tmp_path / (fixture + ".ifc"))
    _cad.stream_step_to_ifc(src, out, "IFC4X3_ADD2", 2.0, 20.0)
    f = ifcopenshell.open(out)
    worst = 0.0
    for i, proxy in enumerate(f.by_type("IfcBuildingElementProxy")):
        brep = proxy.Representation.Representations[0].Items[0]
        if not brep.is_a("IfcAdvancedBrep") or i not in oracle:
            continue
        pts = []
        for face in brep.Outer.CfsFaces:
            for b in face.Bounds:
                lp = b.Bound
                if lp.is_a("IfcEdgeLoop"):
                    for oe in lp.EdgeList:
                        ec = oe.EdgeElement
                        for v in (ec.EdgeStart, ec.EdgeEnd):
                            if v and v.is_a("IfcVertexPoint"):
                                pts.append(v.VertexGeometry.Coordinates)
                elif lp.is_a("IfcPolyLoop"):
                    pts += [p.Coordinates for p in lp.Polygon]
        if not pts:
            continue
        a = np.array(pts)
        omn, omx = oracle[i]
        diag = float(np.linalg.norm(omx - omn)) or 1.0
        cerr = float(np.linalg.norm((a.min(0) + a.max(0)) / 2 - (omn + omx) / 2)) / diag
        serr = float(np.linalg.norm((a.max(0) - a.min(0)) - (omx - omn))) / diag
        worst = max(worst, cerr, serr)
    assert worst < 0.02, f"{fixture}: IFC vs GLB bbox rel-err {worst:.4f} too large"


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("schema", SCHEMAS)
def test_emit_ifc_brep_validates(fixture, schema):
    brep = _cad.step_emit_ifc_brep(_fixture_dir() + fixture, 0, 1000)
    assert brep, f"{fixture}: emitter returned empty (solid skipped)"
    assert "IfcAdvancedBrep(" in brep
    m = re.findall(r"#(\d+)=IfcAdvancedBrep\(", brep)
    assert m, "no IfcAdvancedBrep id"
    f = ifcopenshell.file.from_string(_wrap(brep, m[-1], schema))
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger)
    issues = [str(s.get("message", s)) for s in logger.statements]
    assert not issues, f"{fixture} [{schema}] ifcopenshell.validate issues: {issues[:5]}"
