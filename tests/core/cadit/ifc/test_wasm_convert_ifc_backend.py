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

    def _no_ifcopenshell(src):
        raise AssertionError("the ifcopenshell tessellation ran although the native one is available")

    monkeypatch.setattr(ib, "ifcopenshell_ifc_to_glb_bytes", _no_ifcopenshell)
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


def test_ifc_to_glb_with_ifcopenshell(model, monkeypatch):
    monkeypatch.setenv(ib.ENV_VAR, "ifcopenshell")
    assert wasm_convert.run("ifc", "ifc", "glb", str(model))[:4] == b"glTF"
