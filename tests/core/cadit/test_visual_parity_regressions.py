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
* multi-instance IfcMappedItem (mapped-shape-with-multiple-items): 4 non-uniform-
  scaled instances of one source solid. The OCC ``to_stp`` writer (the parity's
  old STEP leg) collapsed them to 1 — a rigid STEP placement can't carry the
  scale — so the parity now writes STEP via the non-OCC stream writer, which
  emits one analytic solid per instance.
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
    "ifc_files/mapped_shapes/mapped-shape-with-multiple-items.ifc",
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


def test_fem_step_cylinder_strategy_keeps_beams(tmp_path):
    """Audit regression (a beam+plate FEM): the analytic ``cylinder`` merge strategy fused only
    SHELL elements — LINE (beam) elements were silently dropped from the FEM->STEP export (neither
    emitted nor counted skipped), so the STEP bbox lost the beam's extent and parity flagged it."""
    import ada

    a = ada.Assembly("A") / (
        ada.Part("p")
        / [
            ada.Beam("bm", (0, 0, 0), (0, 0, 2), "IPE200"),
            ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01),
        ]
    )
    part = a.get_part("p")
    part.fem = part.to_fem_obj(0.5)

    fem_dir = tmp_path / "fem"
    a.to_fem("beam_plate", "abaqus", scratch_dir=fem_dir)
    b = ada.from_fem(fem_dir / "beam_plate" / "beam_plate.inp")

    out = tmp_path / "out.step"
    stats = b.to_stp(out, writer="stream", fuse_fem=True, merge_strategy="cylinder")
    # the analytic shell (plate faces) AND the fused beam must both emit
    assert stats["emitted"] >= 2, stats
    txt = out.read_text()
    assert "MANIFOLD_SOLID_BREP" in txt  # the beam's extruded solid — absent before the fix


def test_meshopt_packed_glb_measures_like_uncompressed(tmp_path):
    """Audit regression: production GLBs ship EXT_meshopt_compression, which trimesh cannot decode
    (IndexError in _read_buffers) — 4 of 5 parity failures in one sweep were glb=ERR on otherwise
    CONSISTENT geometry. The measurer must unpack meshopt before measuring."""
    pytest.importorskip("adacpp")  # the meshopt codecs live in adacpp
    import ada
    from ada.cadit.visual_parity import _measure_produced_file
    from ada.visit.gltf.meshopt import meshopt_compress_glb

    plain = tmp_path / "plain.glb"
    (ada.Assembly("A") / (ada.Part("p") / ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))).to_gltf(plain)

    packed = tmp_path / "packed.glb"
    assert meshopt_compress_glb(plain, packed, min_bytes=0) == packed  # actually packed, no fallback

    m_plain = _measure_produced_file("glb", plain)
    m_packed = _measure_produced_file("glb", packed)
    assert m_packed.tris == m_plain.tris
    assert m_packed.area == pytest.approx(m_plain.area, rel=1e-6)
    assert m_packed.bbox == pytest.approx(m_plain.bbox, rel=1e-6)
