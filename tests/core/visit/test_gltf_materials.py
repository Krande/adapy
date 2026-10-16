import json
import struct

from ada.visit.gltf.materials import (
    DEFAULT_BASE_COLOR,
    default_black_materials,
    default_black_materials_glb,
)


def _glb(doc: dict, binary: bytes = b"\x00" * 8) -> bytes:
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    bin_chunk = struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body) + len(bin_chunk))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
        + bin_chunk
    )


def _doc(glb: bytes) -> dict:
    (n,) = struct.unpack_from("<I", glb, 12)
    return json.loads(glb[20 : 20 + n])


def test_a_pure_black_material_gets_the_default_colour_and_others_are_kept():
    doc = {
        "materials": [
            {"pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 0.5]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0, 0, 1]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 1], "baseColorTexture": {"index": 0}}},
            {"pbrMetallicRoughness": {}},  # the glTF default, white
        ]
    }
    assert default_black_materials(doc) == 1
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [*DEFAULT_BASE_COLOR, 0.5]
    assert doc["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"] == [0.2, 0, 0, 1]
    assert doc["materials"][2]["pbrMetallicRoughness"]["baseColorFactor"] == [0, 0, 0, 1]


def test_only_the_json_chunk_of_a_glb_changes():
    black = _glb(
        {"asset": {"version": "2.0"}, "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 1]}}]}
    )
    out = default_black_materials_glb(black)
    assert _doc(out)["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][:3] == list(DEFAULT_BASE_COLOR)
    assert out.endswith(struct.pack("<I4s", 8, b"BIN\x00") + b"\x00" * 8)
    assert struct.unpack_from("<I", out, 8)[0] == len(out)
    coloured = _glb(
        {"asset": {"version": "2.0"}, "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}]}
    )
    assert default_black_materials_glb(coloured) is coloured
    assert default_black_materials_glb(b"not a glb") == b"not a glb"
