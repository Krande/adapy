"""The browser conversion reports its progress as it goes.

``ada.cadit.wasm_convert.run`` is what the Pyodide worker runs for every in-browser
conversion; the worker posts each ``on_progress(stage, fraction)`` it makes to the page,
which moves the loading bar. Without them the bar sat at the fixed share the page set on
dispatch until the model appeared. Pinned here: every format reports, the fractions only
grow, the stages say what is happening, and ``("done", 1.0)`` comes last.
"""

from __future__ import annotations

import pathlib

import pytest

from ada.cadit.wasm_progress import ConversionProgress

ROOT = pathlib.Path(__file__).resolve().parents[3]
FILES = ROOT / "files"


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _recorder():
    calls: list[tuple[str, float]] = []
    return calls, lambda stage, fraction: calls.append((stage, fraction))


def test_progress_is_monotonic_and_clamped():
    calls, cb = _recorder()
    clock = _Clock()
    prog = ConversionProgress(cb, clock=clock)
    prog("a", 0.5)
    clock.t += 1
    prog("b", 0.2)  # lower than told before: lifted
    clock.t += 1
    prog("c", 7.0)  # above 1: clamped
    assert [f for _, f in calls] == [0.5, 0.5, 1.0]


def test_progress_throttles_within_a_stage_but_not_across_stages():
    calls, cb = _recorder()
    clock = _Clock()
    prog = ConversionProgress(cb, clock=clock, min_interval=0.1)
    tess = prog.counter("tessellating", 0.3, 0.9, "objects")
    for i in range(1000):  # a per-object loop inside one 50 ms burst
        clock.t += 0.00005
        tess(i, 1000)
    assert len(calls) == 1, "a per-object loop must not post a message per object"
    clock.t += 0.2
    tess(999, 1000)
    # The next post carries the furthest point reached, the held-back ones included.
    assert calls[-1] == ("tessellating (999/1000 objects)", pytest.approx(0.3 + 0.6 * 0.999))
    prog("writing GLB", 0.9)  # a new stage always goes through, however soon
    prog("done", 1.0)
    assert [s for s, _ in calls[-2:]] == ["writing GLB", "done"]


def test_progress_survives_a_failing_callback_and_none():
    def boom(stage, fraction):
        raise RuntimeError("page gone")

    ConversionProgress(boom)("reading", 0.1)
    ConversionProgress(None)("reading", 0.1)


def _run(fmt: str, ext: str, target: str, src: pathlib.Path) -> list[tuple[str, float]]:
    from ada.cadit import wasm_convert

    if not src.is_file():
        pytest.skip(f"fixture missing: {src}")
    calls, cb = _recorder()
    out = wasm_convert.run(fmt, ext, target, str(src), on_progress=cb)
    assert out, "the conversion produced nothing"
    return calls


def _assert_progress(calls: list[tuple[str, float]], *stages: str) -> None:
    assert calls, "no progress was reported"
    fractions = [f for _, f in calls]
    assert fractions == sorted(fractions), f"fractions went backwards: {calls}"
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert calls[-1] == ("done", 1.0)
    assert fractions[0] < 0.5, "the first report must come early, not at the end"
    texts = [s for s, _ in calls]
    for stage in stages:
        assert any(t.startswith(stage) for t in texts), f"no {stage!r} stage in {texts}"


@pytest.mark.parametrize(
    "fixture",
    ["fem_files/sesam/xml_all_basic_props.xml", "fem_files/sesam/plate_flat.xml"],
)
def test_genie_xml_to_glb_reports_reading_tessellating_writing(fixture):
    calls = _run("genie", "xml", "glb", FILES / fixture)
    _assert_progress(calls, "reading Genie XML", "building scene", "tessellating", "writing GLB")
    # Per-object counts: the native record walk ("preparing geometry") or, where the worker's
    # GLB leg takes the Python scene, the tessellation itself.
    counted = [s for s, _ in calls if s.endswith("objects)")]
    assert counted, f"no stage names an object count: {calls}"


def test_sat_to_glb_reports():
    _assert_progress(
        _run("sat", "sat", "glb", FILES / "sat_files" / "flat_plate_sesam_10x10.sat"),
        "reading SAT",
        "tessellating",
        "writing GLB",
    )


def test_fem_deck_to_glb_reports():
    _assert_progress(
        _run("fem", "fem", "glb", FILES / "fem_files" / "sesam" / "beamMassT1.FEM"),
        "reading FEM deck",
        "writing GLB",
    )


def test_fem_deck_to_deck_reports():
    _assert_progress(_run("fem", "fem", "inp", FILES / "fem_files" / "sesam" / "beamMassT1.FEM"), "parsing", "writing")


def test_genie_xml_to_ifc_reports():
    pytest.importorskip("ifcopenshell")
    _assert_progress(
        _run("genie", "xml", "ifc", FILES / "fem_files" / "sesam" / "plate_flat.xml"),
        "reading Genie XML",
        "writing IFC",
    )


def test_ifc_to_glb_reports():
    pytest.importorskip("ifcopenshell")
    _assert_progress(
        _run("ifc", "ifc", "glb", FILES / "ifc_files" / "beams" / "beam-standard-case.ifc"),
        "reading IFC",
        "writing GLB",
    )


def test_step_to_glb_reports():
    import ada.cad

    backend = ada.cad.select_backend(prefer="adacpp")
    if not hasattr(backend, "step_bytes_to_glb_bytes"):
        pytest.skip("the CAD backend has no STEP->GLB pipeline here")
    _assert_progress(_run("step", "stp", "glb", FILES / "step_files" / "plate_1_flat.stp"), "converting STEP")


def test_fea_result_to_glb_reports():
    _assert_progress(
        _run("fea_glb", "sif", "glb", FILES / "fem_files" / "sesam" / "1EL_SHELL_R1.SIF"),
        "reading FEA result",
        "tessellating result mesh",
    )


def test_mesh_passthrough_reports():
    _assert_progress(
        _run("mesh", "glb", "glb", FILES / "gltf_files" / "polygon_vertex_animation.glb"),
        "reading GLB mesh",
        "writing GLB",
    )


def test_run_without_a_callback_still_converts():
    from ada.cadit import wasm_convert

    assert wasm_convert.run("genie", "xml", "glb", str(FILES / "fem_files" / "sesam" / "plate_flat.xml"))
