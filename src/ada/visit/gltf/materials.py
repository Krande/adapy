"""Materials of GLBs ada reads but did not write.

A pure-black base colour is how some exporters say "no colour": it renders as a silhouette. Such a
material gets the default colour, the rule :mod:`ada.visit.gltf.store` applies to what ada writes.
"""

from __future__ import annotations

import json
import struct

from ada.visit.colors import color_dict

#: The default colour as a glTF base colour (0..1).
DEFAULT_BASE_COLOR = tuple(c / 255 for c in color_dict["light-gray"])


def default_black_materials(doc: dict) -> int:
    """Give every untextured, pure-black material of ``doc`` (glTF JSON) the default colour, its
    alpha kept. Returns how many changed."""
    changed = 0
    for mat in doc.get("materials") or []:
        pbr = mat.get("pbrMetallicRoughness")
        if not isinstance(pbr, dict) or mat.get("extensions") or "baseColorTexture" in pbr:
            continue
        factor = pbr.get("baseColorFactor")
        if not isinstance(factor, list) or len(factor) != 4 or any(float(c) != 0 for c in factor[:3]):
            continue
        pbr["baseColorFactor"] = [*DEFAULT_BASE_COLOR, factor[3]]
        changed += 1
    return changed


def default_black_materials_glb(data: bytes) -> bytes:
    """``data`` (a GLB) with :func:`default_black_materials` applied; only the JSON chunk changes,
    so a meshopt-packed GLB stays packed. Unchanged bytes when nothing is black or it is no GLB."""
    if data[:4] != b"glTF":
        return data
    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    if not default_black_materials(doc):
        return data
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    rest = data[20 + json_len :]
    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body) + len(rest))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
        + rest
    )
