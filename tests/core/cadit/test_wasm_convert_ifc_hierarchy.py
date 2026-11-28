"""The browser IFC->GLB conversion keeps the scene hierarchy the viewer's tree reads.

``ada.cadit.wasm_convert`` is the module the Pyodide worker runs (and the node sweep
driver shares). Its IFC->GLB output must carry the scene-extras hierarchy contract
(``id_hierarchy`` + ``draw_ranges_node<n>``), as the server's IFC conversion does:
without it the selection tree of a browser-converted IFC model is empty.
"""

from __future__ import annotations

import json
import pathlib
import struct

import pytest

pytest.importorskip("ifcopenshell")

ROOT = pathlib.Path(__file__).resolve().parents[3]
BEAM = ROOT / "files" / "ifc_files" / "beams" / "beam-standard-case.ifc"


def _scene_extras(glb: bytes) -> dict:
    json_len = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20 : 20 + json_len])
    return doc["scenes"][doc.get("scene", 0)].get("extras") or {}


def test_ifc_to_glb_carries_the_scene_hierarchy():
    from ada.cadit import wasm_convert

    glb = wasm_convert.run("ifc", "ifc", "glb", str(BEAM))
    extras = _scene_extras(bytes(glb))
    assert extras.get("id_hierarchy"), "no id_hierarchy: the selection tree would be empty"
    assert any(k.startswith("draw_ranges_node") for k in extras)
