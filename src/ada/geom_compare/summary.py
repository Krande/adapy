"""Per-element summaries of a merged-mesh GLB, vectorised: box, signed volume, area, triangle count
and a fingerprint, for every element at once.

``glb.elements`` walks triangles in Python, which is right for a test and wrong for a model of
100 000 elements. This reads the same layout (``glb.merged_hierarchy``) with numpy, one draw range at
a time, and never keeps more than one primitive's arrays alive.

THE FINGERPRINT is a hash of the element's triangles as drawn -- vertex positions quantised to
``quantum`` (GLB units) in index order. The same element built the same way hashes the same; any
change to its geometry changes it. A comparison keeps it per element per side, so an element whose
two fingerprints have not moved since the last comparison is not measured again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Mapping

from . import glb as _glb

__all__ = ["ElementSummary", "summarise_glb"]

#: Positions are quantised to this many GLB units (glTF: metres) before hashing: 0.01 mm.
DEFAULT_QUANTUM = 1e-5


@dataclass
class ElementSummary:
    """One element on one side, in the GLB's units (``scale`` applied: mm by default)."""

    key: str
    name: str
    bbox_min: tuple
    bbox_max: tuple
    volume: float
    area: float
    triangles: int
    fingerprint: str
    #: The GLB ids it was folded from (more than one: split across materials, or a repeated name).
    glb_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bbox_min": list(self.bbox_min),
            "bbox_max": list(self.bbox_max),
            "volume": self.volume,
            "area": self.area,
            "triangles": self.triangles,
            "fingerprint": self.fingerprint,
        }


def summarise_glb(
    data: bytes,
    *,
    names: Mapping[str, str] | None = None,
    key: Callable[[str], str] = _glb.normalise,
    scale: float = 1000.0,
    quantum: float = DEFAULT_QUANTUM,
    z_up: bool = True,
) -> dict[str, ElementSummary]:
    """``{key: ElementSummary}`` of every element a GLB draws. ``names`` overrides an entry's name per
    GLB id (``{glb id: name}``); the GLB's ``node_ids`` are NOT applied here (a caller maps them to
    names it trusts). ``scale`` converts GLB units (glTF metres -> mm); ``z_up`` rotates the GLB's
    Y-up frame to Z-up, so boxes read like the model's own coordinates."""
    import numpy as np

    js, binary = _glb.parse_glb(data)
    hierarchy = _glb.merged_hierarchy(js)
    if hierarchy is None:
        return {}
    acc: dict[str, dict] = {}
    for node in js.get("nodes", []):
        spans = hierarchy.ranges.get(node.get("name") or "")
        if not spans or "mesh" not in node:
            continue
        for prim in js["meshes"][node["mesh"]].get("primitives", []):
            attrs = prim.get("attributes") or {}
            if "indices" not in prim or "POSITION" not in attrs:
                continue
            idx = np.frombuffer(
                _glb.accessor_array(js, binary, prim["indices"]).tobytes(), dtype=_dtype(js, prim["indices"])
            )
            pos = np.frombuffer(_glb.accessor_array(js, binary, attrs["POSITION"]).tobytes(), dtype=np.float32)
            pos = pos.reshape(-1, 3).astype(np.float64)
            if z_up:
                pos = np.column_stack([pos[:, 0], -pos[:, 2], pos[:, 1]])
            for eid, (first, count) in spans.items():
                end = min(first + count, len(idx))
                end -= (end - first) % 3
                if end <= first:
                    continue
                tri = idx[first:end].astype(np.int64)
                if tri.max(initial=-1) >= len(pos):
                    tri = tri.reshape(-1, 3)
                    tri = tri[(tri < len(pos)).all(axis=1)].reshape(-1)
                    if not len(tri):
                        continue
                v = pos[tri]
                name = (names or {}).get(eid) or hierarchy.by_id.get(eid, (eid, "*"))[0]
                k = key(name)
                a = acc.get(k)
                if a is None:
                    a = acc[k] = {
                        "name": name,
                        "lo": np.full(3, np.inf),
                        "hi": np.full(3, -np.inf),
                        "vol": 0.0,
                        "area": 0.0,
                        "tris": 0,
                        "hash": hashlib.sha256(),
                        "ids": [],
                    }
                a["lo"] = np.minimum(a["lo"], v.min(axis=0))
                a["hi"] = np.maximum(a["hi"], v.max(axis=0))
                t = v.reshape(-1, 3, 3)
                c = v.mean(axis=0)  # about a local centroid: no precision lost far from the origin
                p0, p1, p2 = t[:, 0] - c, t[:, 1] - c, t[:, 2] - c
                a["vol"] += float(np.einsum("ij,ij->i", p0, np.cross(p1, p2)).sum() / 6.0)
                a["area"] += float(0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum())
                a["tris"] += len(t)
                a["hash"].update(np.round(v / quantum).astype(np.int64).tobytes())
                a["ids"].append(eid)
    out: dict[str, ElementSummary] = {}
    for k, a in acc.items():
        out[k] = ElementSummary(
            key=k,
            name=a["name"],
            bbox_min=tuple(float(x) * scale for x in a["lo"]),
            bbox_max=tuple(float(x) * scale for x in a["hi"]),
            volume=a["vol"] * scale**3,
            area=a["area"] * scale**2,
            triangles=a["tris"],
            fingerprint=a["hash"].hexdigest()[:24],
            glb_ids=a["ids"],
        )
    return out


def _dtype(js: dict, index: int):
    import numpy as np

    return {5121: np.uint8, 5123: np.uint16, 5125: np.uint32}[js["accessors"][index]["componentType"]]
