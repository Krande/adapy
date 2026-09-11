"""The procedural document rides into the GLB even when it carries numpy-backed values."""

from __future__ import annotations

import json
import struct

import numpy as np

import ada
from ada.visit.scene_converter import SceneConverter, _json_safe


def glb_json(glb: bytes) -> dict:
    assert glb[:4] == b"glTF"
    (json_len,) = struct.unpack("<I", glb[12:16])
    return json.loads(glb[20 : 20 + json_len].decode("utf-8"))


def test_json_safe_turns_points_and_arrays_into_lists():
    safe = _json_safe(
        {
            "origin": ada.Point(1, 2, 3),
            "box": np.array([[0, 1], [2, 3]]),
            "scalar": np.float64(2.5),
            "count": np.int64(4),
            "name": "V-201",
            "nested": [ada.Point(0, 0, 1), (np.float32(1.5),)],
        }
    )
    assert safe == {
        "origin": [1.0, 2.0, 3.0],
        "box": [[0, 1], [2, 3]],
        "scalar": 2.5,
        "count": 4,
        "name": "V-201",
        "nested": [[0.0, 0.0, 1.0], [1.5]],
    }
    json.dumps(safe)


def test_a_document_carrying_points_reaches_the_glb():
    asm = ada.Assembly("Doc") / (ada.Part("P") / ada.Beam("bm1", (0, 0, 0), (2, 0, 0), "IPE200"))
    asm.metadata["procedural_doc"] = {
        "spaces": [{"NAME": "Deck1", "origin": ada.Point(0, 0, 0), "DX": np.float64(24.0)}],
        "equipments": [],
    }
    extras = glb_json(SceneConverter(source=asm).build_glb())["asset"]["extras"]
    # ``ada.Point.item()`` raised for size > 1 and the whole document was dropped with a warning.
    assert extras["procedural_doc"]["spaces"][0]["origin"] == [0.0, 0.0, 0.0]
    assert extras["procedural_doc"]["spaces"][0]["DX"] == 24.0
