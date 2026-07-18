"""Cross-format visual-parity validation (ada.cadit.visual_parity).

The same model exported to structure-preserving formats (IFC / Genie XML / STEP)
and reloaded must show the same number of visualized elements. These tests run
purely in-process (no audit stack) on a known-good 4-object assembly.
"""

from pathlib import Path

import pytest
import trimesh

import ada
from ada import Beam, Plate, Section
from ada.cadit import visual_parity
from ada.cadit.visual_parity import (
    ParityResult,
    cross_format_parity,
    visualized_element_count,
)


def _model():
    # A 4-object mix incl. a cylindrical tube — the STEP round-trip reconstructs
    # analytic curved AdvancedFaces, which both OCC and ada-cpp (>=0.7.0, cylindrical/
    # conical/toroidal builders) now build, so parity holds under either backend.
    tub = Beam("tub", (0, 0, 0), (0, 0, 3), Section("tub", from_str="TUB300x20"))
    box = Beam("box", (1, 0, 0), (1, 0, 3), Section("box", from_str="BOX400x400x20x20"))
    ipe = Beam("ipe", (2, 0, 0), (6, 0, 0), Section("ipe", from_str="IPE300"))
    pl = Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    return ada.Assembly("m") / (ada.Part("pp") / [tub, box, ipe, pl])


def test_parity_consistent_on_good_model():
    r = cross_format_parity(_model())

    assert isinstance(r, ParityResult)
    assert r.expected == 4
    assert r.counts["ifc"] == r.counts["xml"] == r.counts["step"] == 4
    assert r.errors == {}
    assert r.mismatches == {}
    assert r.consistent is True


def test_parity_flags_dropped_element(monkeypatch):
    # Wrap the IFC reader so it drops one part on reload -> a real divergence the
    # parity check must catch (deterministic, no dependency on a real converter bug).
    writer, reader, suffix = visual_parity._FORMAT_IO["ifc"]

    def _lossy_reader(path):
        a = reader(path)
        part = next(iter(a.parts.values()))
        # remove one physical object from the loaded model
        victim = next(iter(part.plates))
        part.plates.remove(victim)
        return a

    monkeypatch.setitem(visual_parity._FORMAT_IO, "ifc", (writer, _lossy_reader, suffix))

    r = cross_format_parity(_model(), formats=("ifc", "xml", "step"))

    assert r.consistent is False
    assert r.mismatches.get("ifc") == 3
    # the other formats are unaffected
    assert r.counts["xml"] == 4
    assert r.counts["step"] == 4


def test_parity_records_reader_errors(monkeypatch):
    writer, reader, suffix = visual_parity._FORMAT_IO["xml"]

    def _boom_reader(path):
        raise RuntimeError("synthetic reader failure")

    monkeypatch.setitem(visual_parity._FORMAT_IO, "xml", (writer, _boom_reader, suffix))

    r = cross_format_parity(_model(), formats=("ifc", "xml", "step"))

    assert "xml" in r.errors
    assert "synthetic reader failure" in r.errors["xml"]
    assert r.consistent is False
    # other formats still produce counts despite the one failure
    assert r.counts["ifc"] == 4
    assert r.counts["step"] == 4


def test_visualized_element_count_excludes_placeholder():
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box((1, 1, 1)), node_name="real")
    scene.add_geometry(trimesh.PointCloud([[0, 0, 0]]), node_name="empty")

    assert visualized_element_count(scene) == 1


def test_parity_for_source_file_fem_offline_geometry(tmp_path):
    """The offline FEM parity fallback derives the PRODUCTION outputs (analytic
    cylinder for step/ifc/xml, to_gltf for glb) and compares the geometry invariant
    — NOT the retired merge_strategy=None + entity-count design. A clean plate-only
    FEM source round-trips consistently: every format spans the same bounding box.

    (The counts are now per-format ``{"area","bbox","tris"}`` geometry measures, so
    the old ``counts[ifc] == counts[step]`` entity-count assertion is gone — the
    formats use different dimensional representations, e.g. STEP emits a plate as a
    mid-surface while Genie-XML emits it as a thin solid, so their areas legitimately
    differ while the geometry is the same.)"""
    inp = _fem_plate_inp(tmp_path)
    r = visual_parity.parity_for_source_file(inp)
    assert r.errors == {}
    assert r.consistent is True
    # bbox agrees across the produced formats (the representation-independent gate)
    bboxes = [m["bbox"] for m in r.counts.values()]
    assert max(bboxes) - min(bboxes) < 0.02 * max(bboxes)


def test_parity_skips_xml_for_generic_solid_source():
    """A source made of generic solids (no Beam/Plate) can't round-trip through
    Genie XML — it carries only structural concepts. Parity must SKIP xml (a
    permanent format limit, not a converter fault) and stay consistent on the
    formats that can carry solids."""
    box = ada.PrimBox("b", (0, 0, 0), (1, 1, 1))
    asm = ada.Assembly("m") / (ada.Part("p") / box)

    r = cross_format_parity(asm, ("ifc", "xml", "step"))

    assert "xml" in r.skipped
    assert "xml" not in r.counts and "xml" not in r.mismatches
    assert r.counts["ifc"] == r.counts["step"] == r.expected
    assert r.consistent is True


def test_parity_for_step_file_uses_writer_counts(tmp_path):
    """The STEP fast-path counts placed instances from the streaming writers'
    own emission (no re-parse of the multi-GB outputs). A clean STEP source must
    round-trip consistently and skip xml (raw B-rep has no Genie concept)."""
    from ada.cadit.visual_parity import parity_for_step_file

    src = tmp_path / "boxes.step"
    (ada.Assembly("m") / (ada.Part("p") / [ada.PrimBox("b1", (0, 0, 0), (1, 1, 1))])).to_stp(src)

    r = parity_for_step_file(src, ("ifc", "xml", "step"))

    assert r.expected >= 1  # source instance count, from the writers' parse
    assert r.counts["source"] == r.expected
    assert r.counts["step"] == r.expected  # nothing dropped by the STEP re-emit
    assert r.counts["ifc"] == r.expected  # nor by the IFC emit
    assert "xml" in r.skipped
    assert r.consistent is True


def test_parity_for_step_file_flags_a_dropped_solid(tmp_path, monkeypatch):
    """When the IFC writer drops a solid (emits fewer instances than it saw), parity
    must flag ifc as a mismatch — the drop is detected from the writer's own
    instances/total_instances split, no output re-scan."""
    from ada.cadit import visual_parity as vp

    src = tmp_path / "one.step"
    (ada.Assembly("m") / (ada.Part("p") / [ada.PrimBox("b1", (0, 0, 0), (1, 1, 1))])).to_stp(src)

    def _drop_ifc(path, out_path, **_kw):
        # A writer that saw 1 instance but emitted 0 (unsupported geometry dropped).
        open(out_path, "w").close()
        return {"emitted": 0, "skipped": 1, "total": 1, "instances": 0, "total_instances": 1, "reasons": {}}

    # Force the per-writer path (the native step_parity fast-path would bypass the writers),
    # then patch the source module — parity_for_step_file imports the writer locally each call,
    # so the `from ... import` re-binds from there at call time.
    monkeypatch.setattr(vp, "_native_step_parity", lambda *a, **k: None)
    monkeypatch.setattr("ada.cadit.step.write.stream_step_to_ifc.stream_step_to_ifc", _drop_ifc)

    r = vp.parity_for_step_file(src, ("ifc", "step"))

    assert r.counts["ifc"] == 0
    assert r.expected >= 1
    assert "ifc" in r.mismatches
    assert r.consistent is False


def _has_step_parity() -> bool:
    try:
        import adacpp

        from ada.cadit.step.read.native_reader import native_adacpp_step_available

        return native_adacpp_step_available() and hasattr(adacpp.cad, "step_parity")
    except Exception:
        return False


@pytest.mark.skipif(not _has_step_parity(), reason="adacpp.cad.step_parity unavailable (pre-branch / no overlay)")
def test_native_step_parity_single_parse_matches(tmp_path):
    """The native single-parse fan-out (adacpp.cad.step_parity) returns a consistent
    ParityResult on a clean STEP source: source == ifc == step instance counts, xml skipped."""
    from ada.cadit.visual_parity import _native_step_parity

    src = tmp_path / "boxes.step"
    (ada.Assembly("m") / (ada.Part("p") / [ada.PrimBox("b1", (0, 0, 0), (1, 1, 1))])).to_stp(src)

    r = _native_step_parity(src, ("ifc", "xml", "step"))
    assert r is not None  # the verb is present (guarded by the skipif)
    assert r.expected >= 1
    assert r.counts["source"] == r.counts["ifc"] == r.counts["step"] == r.expected
    assert "xml" in r.skipped
    assert r.consistent is True


def _fem_plate_inp(tmp_path):
    """Write a small plate-only FEM to Abaqus .inp and return its path. Plate-only
    (no line beams) so every produced format — including the STEP stream writer —
    carries the geometry, giving a clean cross-format consistency baseline."""
    import glob

    pl = Plate("P", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    part = ada.Part("pp")
    part.fem = pl.to_fem_obj(0.5, "shell")
    (ada.Assembly("A") / part).to_fem("m", "abaqus", scratch_dir=str(tmp_path), overwrite=True)
    return glob.glob(str(tmp_path / "**" / "*.inp"), recursive=True)[0]


# ── Geometry-invariant parity over produced files ───────────────────────────
#
# These pin the verdict logic deterministically by feeding crafted geometry
# measures (no heavy tessellation), then a couple of real-file smoke tests.


def _gm(area, bbox, tris=100, empty=False):
    from ada.cadit.visual_parity import _GeomMeasure

    return _GeomMeasure(area=area, bbox=bbox, tris=tris, empty=empty)


def _run_verdict(monkeypatch, measures):
    """Run parity_from_produced_files with _measure_produced_file stubbed to return
    ``measures`` (a {fmt: _GeomMeasure} map)."""
    from ada.cadit import visual_parity as vp

    monkeypatch.setattr(vp, "_measure_produced_file", lambda fmt, path: measures[fmt])
    produced = {fmt: Path(f"produced.{fmt}") for fmt in measures}
    return vp.parity_from_produced_files("m.fem", produced)


def test_produced_verdict_consistent_across_representations(monkeypatch):
    """The KEY design property: absolute AREA legitimately differs across formats
    that use different dimensional representations (STEP mid-surface = 2, Genie-XML
    thin solid = 4.12, glb mesh = 2) yet the model is identical. The bbox is the
    same for all, and the area floor sits well below the ~0.5 solid-vs-surface
    ratio — so this must NOT flag a mismatch."""
    r = _run_verdict(
        monkeypatch,
        {"step": _gm(2.0, 5.0, 2), "ifc": _gm(2.0, 5.0, 2), "xml": _gm(4.12, 5.0, 12), "glb": _gm(2.0, 5.0, 16)},
    )
    assert r.consistent is True
    assert r.mismatches == {} and r.errors == {}
    # counts carry the per-format geometry measure, not an entity count
    assert r.counts["xml"] == {"area": 4.12, "bbox": 5.0, "tris": 12}


def test_produced_verdict_flags_shrunk_bbox(monkeypatch):
    """A format that dropped a solid/region shrinks its bounding box — the strict,
    representation-independent gate flags it (here the shipped glb, which the viewer
    loads: a drop there is the worst case)."""
    r = _run_verdict(
        monkeypatch,
        {"ifc": _gm(10.0, 10.0), "step": _gm(10.0, 10.0), "glb": _gm(6.0, 8.0)},  # glb bbox -20%
    )
    assert r.consistent is False
    assert "glb" in r.mismatches and "bbox" in r.mismatches["glb"]
    assert "ifc" not in r.mismatches and "step" not in r.mismatches


def test_produced_verdict_area_floor_catches_gross_drop(monkeypatch):
    """A CAD format that keeps its bbox but grossly loses surface area (near-empty)
    is caught by the coarse area floor — a backstop below the bbox gate."""
    r = _run_verdict(
        monkeypatch,
        {"ifc": _gm(10.0, 10.0), "step": _gm(0.5, 10.0), "glb": _gm(8.0, 10.0)},  # step area 0.5 << 10
    )
    assert r.consistent is False
    assert "step" in r.mismatches and "area" in r.mismatches["step"]


def test_produced_verdict_flags_empty(monkeypatch):
    """A format that produced no renderable geometry at all (empty scene / IFC that
    imported nothing) is flagged."""
    r = _run_verdict(monkeypatch, {"ifc": _gm(10.0, 10.0), "glb": _gm(0.0, 0.0, 0, empty=True)})
    assert r.consistent is False
    assert "glb" in r.mismatches and "no renderable geometry" in r.mismatches["glb"]


def test_produced_verdict_skips_solid_only_concepts(monkeypatch):
    """A solid-only / mesh-only FEM has no shells or beams to reconstruct, so its
    step/ifc/xml exports are legitimately EMPTY while the glb still carries the
    element mesh. The empty concept formats are SKIPPED (not flagged as a drop),
    and the source is consistent — this is the class that erroneously failed every
    solid FEM parity cell."""
    r = _run_verdict(
        monkeypatch,
        {
            "step": _gm(0.0, 0.0, 0, empty=True),
            "ifc": _gm(0.0, 0.0, 0, empty=True),
            "xml": _gm(0.0, 0.0, 0, empty=True),
            "glb": _gm(1.2, 0.3, 6000),
        },
    )
    assert r.consistent is True
    assert r.mismatches == {} and r.errors == {}
    assert set(r.skipped) == {"step", "ifc", "xml"}


def test_produced_verdict_records_missing_format_without_rederiving(monkeypatch):
    """A format whose conversion failed/was skipped comes in as None: it is RECORDED
    in ``skipped`` (never re-derived) and excluded from the verdict — the present
    formats still decide consistency."""
    from ada.cadit import visual_parity as vp

    measures = {"ifc": _gm(10.0, 10.0), "glb": _gm(10.0, 10.0)}
    monkeypatch.setattr(vp, "_measure_produced_file", lambda fmt, path: measures[fmt])
    produced = {"ifc": Path("produced.ifc"), "glb": Path("produced.glb"), "step": None}
    r = vp.parity_from_produced_files("m.fem", produced)
    assert r.consistent is True
    assert "step" in r.skipped
    assert "step" not in r.counts and "step" not in r.mismatches


def test_measure_produced_file_reads_glb_directly(tmp_path):
    """Smoke test the mesh-direct measure path on a real GLB: a 2×1×3 box scene
    reports the expected bbox diagonal and a positive area/triangle count."""
    from ada.cadit.visual_parity import _measure_produced_file

    p = tmp_path / "box.glb"
    trimesh.Scene(trimesh.creation.box((2, 1, 3))).export(str(p))
    m = _measure_produced_file("glb", p)
    assert m.tris > 0 and m.area > 0
    assert abs(m.bbox - (2**2 + 1**2 + 3**2) ** 0.5) < 1e-6
    assert m.empty is False
