"""The Pyodide converter's IFC legs go through the IFC backend: native where it covers the job."""

from __future__ import annotations

import pytest

import ada
from ada.cadit import wasm_convert
from ada.cadit.ifc import backend as ib

_native_glb = ib.CAP_TESSELLATE_GLB in ib.NativeIfcBackend().capabilities()


@pytest.fixture
def model(tmp_path):
    src = tmp_path / "m.ifc"
    beams = [ada.Beam(f"bm{i}", (i, 0, 0), (i, 0, 1), "IPE300") for i in range(2)]
    (ada.Assembly("a") / (ada.Part("p") / beams)).to_ifc(src)
    return src


@pytest.mark.skipif(not _native_glb, reason="adacpp without stream_ifc_to_glb")
def test_ifc_to_glb_uses_the_native_backend(model, monkeypatch):
    monkeypatch.delenv(ib.ENV_VAR, raising=False)

    def _not_used(*a, **k):
        raise AssertionError("a Python IFC path ran although the native tessellation is available")

    monkeypatch.setattr(wasm_convert, "_read_source", _not_used)
    monkeypatch.setattr(wasm_convert, "_ifc_to_glb", _not_used)
    glb = wasm_convert.run("ifc", "ifc", "glb", str(model))
    assert glb[:4] == b"glTF"
    stl = wasm_convert.run("ifc", "ifc", "stl", str(model))
    assert len(stl) > 0


@pytest.mark.skipif(not _native_glb, reason="adacpp without stream_ifc_to_glb")
def test_ifc_to_glb_falls_back_when_native_fails(model, monkeypatch):
    monkeypatch.delenv(ib.ENV_VAR, raising=False)

    def _boom(self, *a, **k):
        raise RuntimeError("native failure")

    monkeypatch.setattr(ib.NativeIfcBackend, "tessellate", _boom)
    assert wasm_convert.run("ifc", "ifc", "glb", str(model))[:4] == b"glTF"


def test_ifc_to_glb_falls_back_to_ifcopenshell_when_adapy_fails(model, monkeypatch):
    monkeypatch.setenv(ib.ENV_VAR, "ifcopenshell")  # no native tessellation

    def _boom(*a, **k):
        raise RuntimeError("adapy failure")

    monkeypatch.setattr(wasm_convert, "_read_source", _boom)
    assert wasm_convert.run("ifc", "ifc", "glb", str(model))[:4] == b"glTF"


def test_ifc_to_glb_with_ifcopenshell(model, monkeypatch):
    monkeypatch.setenv(ib.ENV_VAR, "ifcopenshell")
    assert wasm_convert.run("ifc", "ifc", "glb", str(model))[:4] == b"glTF"
