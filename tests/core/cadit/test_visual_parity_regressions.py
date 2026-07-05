"""Cross-format visual-parity regressions from the corpus audit sweep.

Each file below previously lost geometry in at least one structure-preserving
export (audit run 63 parity phase):

* revolved / varying-extrusion-path beams: BeamRevolve dropped by the DOM
  Genie-XML writer (only the streaming writer chord-fied them)
* FixedReferenceSweptAreaSolid: GradientCurve directrix unbuildable ->
  skipped in STEP export
* SAT wire bodies: bare curve shapes routed through a pythonocc-only wire
  builder (skipped under the adacpp backend), and the native STEP counter
  saw 0 roots in wireframe-only outputs
* SAT b-spline shell: written as a bare IfcClosedShell the reader could not
  import back
* IfcTriangulatedFaceSet: no B-rep build existed, so IFC re-export needed an
  impossible kernel round-trip and STEP export wrote no solid
"""

import pytest

from ada.cadit.visual_parity import parity_for_source_file

PARITY_SOURCES = [
    "ifc_files/beams/beam-revolved-solid.ifc",
    "ifc_files/beams/beam-varying-extrusion-paths.ifc",
    "ifc_files/fixed-reference-swept-area-solid.ifc",
    "sat_files/single_beam_sesam.sat",
    "sat_files/bsplinesurfacewithknots.sat",
    "fem_files/sesam/xml_all_basic_props.sat",
    "ifc_files/bs_samples/tessellation-with-image-texture.ifc",
    "ifc_files/bs_samples/column-straight-rectangle-tessellation.ifc",
]


@pytest.mark.parametrize("rel_path", PARITY_SOURCES)
def test_cross_format_parity(example_files, monkeypatch, rel_path):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    import ada

    ada.config.Config().reload_config()

    res = parity_for_source_file(example_files / rel_path, ("ifc", "xml", "step"))
    assert res.consistent, f"{rel_path}: counts={res.counts} mismatches={res.mismatches} errors={res.errors}"


def test_empty_source_counts_zero_everywhere(example_files, tmp_path):
    """A geometry-less model must count 0 on every leg. The STEP leg regressed
    to 1 once (audit run 64): the wireframe-blind native counter fell back to a
    reload, and the reloaded empty file materialized one zero-vertex Shape."""
    import ada
    from ada.cadit.visual_parity import assembly_element_count, cross_format_parity

    res = cross_format_parity(ada.Assembly("Empty"), ("ifc", "step"), work_dir=tmp_path)
    assert res.counts == {"source": 0, "ifc": 0, "step": 0}
    assert res.consistent, f"counts={res.counts} mismatches={res.mismatches} errors={res.errors}"

    # And a wire-only STEP must still count its wireframe as 1 (not 0, not a
    # zero-vertex phantom): GEOMETRIC_CURVE_SET is a stream-reader root.
    a = ada.from_acis(example_files / "sat_files/single_beam_sesam.sat")
    out = tmp_path / "wire.step"
    a.to_stp(out)
    assert assembly_element_count(ada.from_step(out, reader="auto")) == 1
