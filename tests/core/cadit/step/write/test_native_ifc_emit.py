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
