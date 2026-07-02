"""FEM sources must be routed through the streaming STEP writer.

The OCC XCAF writer OOMs on large FEM meshes; the streaming AP242 writer holds
constant memory. `_via_ada_to_step` should select `writer="stream"` for FEM
sources and keep the default OCC writer for everything else.
"""

import pathlib

import ada
from ada.api.spatial.part import Part
from ada.comms.rest import converter as conv


def _captured_writer(monkeypatch, source_ext: str) -> str:
    model = ada.Assembly("a") / (ada.Part("p") / ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))

    monkeypatch.setattr(conv, "_load_with_ada", lambda src, ext: model)
    monkeypatch.setattr(conv, "_apply_fem_to_objects", lambda *a, **k: None)

    recorded = {}

    def fake_to_stp(self, dest, *args, writer="occ", **kwargs):
        recorded["writer"] = writer
        pathlib.Path(dest).write_text("ISO-10303-21;\nENDSEC;\nEND-ISO-10303-21;\n")
        return {"emitted": 1, "skipped": 0}

    monkeypatch.setattr(Part, "to_stp", fake_to_stp)

    out = conv._via_ada_to_step(pathlib.Path(f"dummy{source_ext}"), source_ext, lambda *a, **k: None)
    # _via_ada_to_step now returns the path of the STEP file it wrote (ownership
    # transfers to the caller); the streaming-upload contract.
    assert isinstance(out, pathlib.Path)
    assert out.read_bytes()  # the fake writer wrote a non-empty deck
    out.unlink()
    return recorded["writer"]


def test_fem_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".fem") == "stream"


def test_inp_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".inp") == "stream"


def test_non_fem_source_uses_occ_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".step") == "occ"
    assert _captured_writer(monkeypatch, ".ifc") == "occ"


def test_glb_target_exposes_tessellation_quality_options():
    """The configurable tessellation-quality knobs are surfaced on any → GLB pair and
    map to the ADA_OCC_TESS_* env the worker / tessellator read."""
    names = {o["name"] for o in conv.ConverterRegistry.options_for(".step", "glb")}
    assert {"tess_linear_deflection", "tess_angular_deg", "tess_relative"} <= names
    # and they are in the global allowlist used by the API validator
    assert "tess_linear_deflection" in conv.ConverterRegistry.all_options()
