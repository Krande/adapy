"""Cross-format visual-parity validation (ada.cadit.visual_parity).

The same model exported to structure-preserving formats (IFC / Genie XML / STEP)
and reloaded must show the same number of visualized elements. These tests run
purely in-process (no audit stack) on a known-good 4-object assembly.
"""

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


def test_parity_fem_source_rebuilds_objects(fem_files):
    """A FEM source must rebuild Beam/Plate concept objects (as the converter
    does) before the round-trip; the writers emit concepts, not the raw mesh.
    Previously parity exported an objectless assembly and read every format back
    empty (a false "all geometry dropped"). After the fix the source and every
    format agree."""
    r = visual_parity.parity_for_source_file(fem_files / "sesam/beamMassT1.FEM", ("ifc", "xml", "step"))
    assert r.expected > 0
    assert r.counts["ifc"] == r.counts["xml"] == r.counts["step"] == r.expected
    assert r.errors == {}
    assert r.consistent is True


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


def _fem_mixed_inp(tmp_path):
    """Write a small FEM (shells + line beams in one part) to Abaqus .inp and return
    its path — the fused-from-mesh source parity_for_fem_file streams over."""
    import glob

    pl = Plate("P", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    bm = Beam("B", (0, 0, 0), (3, 0, 0), "IPE300")
    part = ada.Part("pp")
    part.fem = pl.to_fem_obj(0.5, "shell")
    part.fem += bm.to_fem_obj(0.5, "line")
    (ada.Assembly("A") / part).to_fem("m", "abaqus", scratch_dir=str(tmp_path), overwrite=True)
    return glob.glob(str(tmp_path / "**" / "*.inp"), recursive=True)[0]


def test_parity_for_fem_file_streams_baseline(tmp_path):
    """Criterion 1: the baseline is counted by STREAMING the FEM-fused object pass
    (no create_objects_from_fem materialisation), and every streaming export
    (ifc/xml/step) fuses the SAME objects straight from the mesh — so source == ifc
    == xml == step with no false mismatch. Exercises a mixed shell+beam part, which
    also guards the no-cross-format-mutation (fresh-reload) invariant."""
    from ada.cadit.visual_parity import _streaming_baseline_count, parity_for_fem_file

    inp = _fem_mixed_inp(tmp_path)

    # the baseline never materialises the part's concept containers
    asm = ada.from_fem(inp)
    baseline = _streaming_baseline_count(asm)
    assert baseline > 1
    for p in asm.get_all_parts_in_assembly(include_self=True):
        assert not len(p.plates) and not len(p.beams)  # streamed, never held whole

    r = parity_for_fem_file(inp)
    assert r.counts["source"] == baseline
    assert r.counts["ifc"] == r.counts["xml"] == r.counts["step"] == baseline
    assert r.errors == {} and r.skipped == {}
    assert r.consistent is True


def test_parity_baseline_reuses_step_stream(tmp_path, monkeypatch):
    """Task B: when STEP is exported, its stream's {emitted+skipped} total IS the
    baseline, so no separate _streaming_baseline_count pass runs; without STEP the
    dedicated count is the fallback. Result stays consistent either way."""
    from ada.cadit import visual_parity

    inp = _fem_mixed_inp(tmp_path)

    calls = {"n": 0}
    real = visual_parity._streaming_baseline_count

    def _counting(asm, merge_strategy=None):
        calls["n"] += 1
        return real(asm, merge_strategy=merge_strategy)

    monkeypatch.setattr(visual_parity, "_streaming_baseline_count", _counting)

    # STEP present -> baseline comes from the STEP export, no extra counting pass
    r = visual_parity.parity_for_fem_file(inp, formats=("ifc", "xml", "step"))
    assert calls["n"] == 0
    assert r.counts["source"] == r.counts["step"] == r.counts["ifc"] == r.counts["xml"]
    assert r.consistent is True

    # STEP absent -> the dedicated streaming count is the fallback (runs once)
    r2 = visual_parity.parity_for_fem_file(inp, formats=("ifc", "xml"))
    assert calls["n"] == 1
    assert r2.counts["source"] == r2.counts["ifc"] == r2.counts["xml"]
    assert r2.consistent is True
