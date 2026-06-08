"""Cross-format visual-parity validation (ada.cadit.visual_parity).

The same model exported to structure-preserving formats (IFC / Genie XML / STEP)
and reloaded must show the same number of visualized elements. These tests run
purely in-process (no audit stack) on a known-good 4-object assembly.
"""

import trimesh

import ada
from ada import Plate
from ada.cadit import visual_parity
from ada.cadit.visual_parity import (
    ParityResult,
    cross_format_parity,
    visualized_element_count,
)


def _model():
    # 4 planar plates: all-planar so the STEP round-trip builds under every CAD
    # backend (released adacpp only ports planar/B-spline AdvancedFaces, not the
    # analytic curved surfaces a beam/tube would reconstruct to). Parity is about
    # element counts across formats, not shape variety.
    plates = [
        Plate(f"pl{i}", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02, origin=(0, 0, float(i)))
        for i in range(4)
    ]
    return ada.Assembly("m") / (ada.Part("pp") / plates)


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
