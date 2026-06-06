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
    assert out  # bytes returned
    return recorded["writer"]


def test_fem_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".fem") == "stream"


def test_inp_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".inp") == "stream"


def test_non_fem_source_uses_occ_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".step") == "occ"
    assert _captured_writer(monkeypatch, ".ifc") == "occ"
