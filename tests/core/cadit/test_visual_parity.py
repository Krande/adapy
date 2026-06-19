"""Cross-format visual-parity validation (ada.cadit.visual_parity).

The same model exported to structure-preserving formats (IFC / Genie XML / STEP)
and reloaded must show the same number of visualized elements. These tests run
purely in-process (no audit stack) on a known-good 4-object assembly.
"""

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
