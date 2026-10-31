"""Per-element geometry out of a merged-mesh GLB.

THE CONVENTION. adapy's GLB writer (``ada.visit.gltf``), and other pipelines that write the same
layout, MERGE all geometry into one mesh per material -- a model of 13 000 elements has a handful of
nodes named ``node0``, ``node1``, ... -- and put element identity in ``scenes[0].extras``:

    id_hierarchy          {id: [name, parent_id]}, parent ``"*"`` at the top
    draw_ranges_node<N>   {id: [first_index, index_count]} into that node's index accessor --
                          ordinary drawElements semantics
    node_ids              {id: published node id} -- written by a core build only (see
                          ``node_ids_of``): which spine row each entry is

So "the geometry of element X" is a slice of an index buffer plus the vertex positions it reaches,
and a mesh loader has thrown away the byte ranges needed to find it by the time it hands back a
scene. Hence this parser is written against the glTF spec rather than pulled from a library.

STDLIB ONLY, deliberately: the reader is the half of a comparison that most needs tests, and it must
run where no numpy or CAD kernel is installed.
"""

from __future__ import annotations

import array
import gzip
import json
import math
import pathlib
import struct
from typing import Iterable, Mapping, NamedTuple

__all__ = [
    "Bounds",
    "Element",
    "ElementMesh",
    "Hierarchy",
    "accessor_array",
    "closure",
    "element_meshes",
    "elements",
    "index_by_name",
    "load",
    "merged_hierarchy",
    "node_ids_of",
    "normalise",
    "parse_glb",
    "read_glb",
    "write",
    "write_glb",
    "y_up_to_z_up",
]

#: glTF componentType -> (array typecode, bytes per component).
_COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}

_PER_TYPE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Bounds(NamedTuple):
    """An axis-aligned box in the GLB's own units and axes (glTF: metres)."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def centre(self) -> tuple[float, float, float]:
        return tuple((a + b) / 2.0 for a, b in zip(self.min, self.max))

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(b - a for a, b in zip(self.min, self.max))


class Element(NamedTuple):
    """One element's tessellated geometry, summarised as the GLB carries it."""

    name: str
    bounds: Bounds
    triangles: int
    #: Signed volume of the element's triangle soup. Meaningful for a closed solid; a surface that is
    #: not closed gives a number that is merely not useful rather than an error.
    volume: float


class ElementMesh(NamedTuple):
    """One element's triangles: flat ``positions`` (x, y, z, ...) and ``indices`` into them (three per
    triangle), in the GLB's units and axes."""

    name: str
    positions: list
    indices: list


def read_glb(path: "pathlib.Path | str") -> bytes:
    """The GLB bytes, inflating them when they were stored gzipped (blob stores often keep them so)."""
    raw = pathlib.Path(path).read_bytes()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def parse_glb(data: bytes) -> tuple[dict, bytes]:
    """The glTF JSON chunk and the binary chunk, from a .glb container (gzipped bytes accepted)."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:4] != b"glTF":
        raise ValueError("not a GLB (missing glTF magic)")
    offset = 12
    js: dict = {}
    binary = b""
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8 : offset + 8 + chunk_len]
        if chunk_type == b"JSON":
            js = json.loads(chunk.decode("utf-8"))
        elif chunk_type == b"BIN\x00":
            binary = chunk
        offset += 8 + chunk_len + (-chunk_len % 4)
    if not js:
        raise ValueError("GLB has no JSON chunk")
    return js, binary


def accessor_array(js: dict, binary: bytes, index: int) -> array.array:
    """One accessor as a flat array, components in file order.

    Refuses a strided bufferView rather than mis-reading it: treating interleaved bytes as tightly
    packed would produce plausible-looking wrong geometry -- the worst outcome for a tool whose whole
    job is to say whether two geometries agree."""
    accessor = js["accessors"][index]
    view = js["bufferViews"][accessor["bufferView"]]
    typecode, size = _COMPONENT[accessor["componentType"]]
    per = _PER_TYPE[accessor["type"]]
    stride = view.get("byteStride")
    if stride is not None and stride != per * size:
        raise ValueError(
            f"accessor {index} is interleaved (byteStride={stride}, tight={per * size}); "
            "this reader only handles tightly packed accessors"
        )
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    out = array.array(typecode)
    out.frombytes(binary[start : start + accessor["count"] * per * size])
    return out


class Hierarchy(NamedTuple):
    #: element id -> (name, parent id). Parent ``"*"`` is the world.
    by_id: dict[str, tuple[str, str]]
    #: glTF node name -> {element id: (first_index, index_count)}
    ranges: dict[str, dict[str, tuple[int, int]]]


def _extras_holders(js: dict) -> list[dict]:
    return (js.get("scenes") or [{}])[:1] + [js, js.get("asset") or {}]


def merged_hierarchy(js: dict) -> Hierarchy | None:
    """``scenes[0].extras``, or None when this GLB does not carry it -- a GLB from another pipeline,
    which a caller reports as "nothing to compare" (a finding, not a crash)."""
    for holder in _extras_holders(js):
        extras = holder.get("extras") or {}
        hierarchy = extras.get("id_hierarchy")
        if not hierarchy:
            continue
        by_id = {
            str(k): (str(v[0]), str(v[1]) if len(v) > 1 else "*")
            for k, v in hierarchy.items()
            if isinstance(v, (list, tuple)) and v
        }
        ranges = {
            key[len("draw_ranges_") :]: {
                str(eid): (int(span[0]), int(span[1]))
                for eid, span in value.items()
                if isinstance(span, (list, tuple)) and len(span) >= 2
            }
            for key, value in extras.items()
            if key.startswith("draw_ranges_") and isinstance(value, dict)
        }
        return Hierarchy(by_id, ranges)
    return None


def node_ids_of(js: dict) -> dict[str, str]:
    """``{glb id: published node id}`` from ``scenes[0].extras.node_ids`` (a core build writes it),
    or empty."""
    for holder in _extras_holders(js):
        ids = (holder.get("extras") or {}).get("node_ids")
        if isinstance(ids, dict):
            return {str(k): str(v) for k, v in ids.items() if v is not None}
    return {}


def _spans(js: dict, binary: bytes, hierarchy: Hierarchy):
    """``(element id, indices, positions, first, count)`` for every draw range, accessors read once
    per primitive."""
    for node in js.get("nodes", []):
        spans = hierarchy.ranges.get(node.get("name") or "")
        if not spans or "mesh" not in node:
            continue
        for prim in js["meshes"][node["mesh"]].get("primitives", []):
            attrs = prim.get("attributes") or {}
            if "indices" not in prim or "POSITION" not in attrs:
                continue
            indices = accessor_array(js, binary, prim["indices"])
            pos = accessor_array(js, binary, attrs["POSITION"])
            for eid, (first, count) in spans.items():
                if count > 0:
                    yield eid, indices, pos, first, count


def elements(
    js: dict, binary: bytes, hierarchy: Hierarchy, *, names: Mapping[str, str] | None = None
) -> dict[str, Element]:
    """``name -> Element``, folding an element split across material nodes.

    An element with two materials is two draw ranges in two merged meshes: widening the box and
    adding the triangles makes it one element again. A NAME CAN REPEAT (``id_hierarchy`` is keyed by
    id); repeats are folded the same way, so a changed twin cannot hide behind a quiet one.
    ``names`` (``{glb id: name}``) overrides the hierarchy's name per id -- a caller that knows each
    entry's identity better (a published node's full name) joins on that."""
    acc: dict[str, list] = {}
    for eid, indices, pos, first, count in _spans(js, binary, hierarchy):
        name = (names or {}).get(eid) or hierarchy.by_id.get(eid, (eid, "*"))[0]
        _accumulate(acc.setdefault(name, _blank()), indices, pos, first, count)
    return {name: Element(name, Bounds(tuple(a[0]), tuple(a[1])), a[2], a[3]) for name, a in acc.items() if a[2] > 0}


def element_meshes(
    js: dict,
    binary: bytes,
    hierarchy: Hierarchy,
    wanted: Iterable[str],
    *,
    names: Mapping[str, str] | None = None,
    key=None,
) -> dict[str, ElementMesh]:
    """The triangles of the elements whose name (``key(name)`` when given) is in ``wanted``, each
    element's draw ranges folded into one mesh with its own compact vertex list."""
    want = set(wanted)
    keyf = key or (lambda n: n)
    out: dict[str, tuple[list, list, dict]] = {}
    for eid, indices, pos, first, count in _spans(js, binary, hierarchy):
        name = (names or {}).get(eid) or hierarchy.by_id.get(eid, (eid, "*"))[0]
        k = keyf(name)
        if k not in want:
            continue
        positions, tris, remap = out.setdefault(k, ([], [], {}))
        end = min(first + count, len(indices))
        end -= (end - first) % 3
        for i in range(first, end, 3):
            tri = []
            for j in range(3):
                v = indices[i + j]
                if v * 3 + 2 >= len(pos):
                    tri = []
                    break
                local = remap.get(v)
                if local is None:
                    local = remap[v] = len(positions) // 3
                    positions.extend((pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]))
                tri.append(local)
            if tri:
                tris.extend(tri)
    return {k: ElementMesh(k, p, t) for k, (p, t, _) in out.items() if t}


def closure(mesh: ElementMesh, weld: float = 1e-5) -> bool:
    """Whether ``mesh`` is closed: every edge -- vertices welded by position, to ``weld`` -- shared by
    an even number of its triangles. A signed volume alone does not tell an open mesh."""
    from collections import Counter

    p = mesh.positions

    def key(v: int):
        return (round(p[v * 3] / weld), round(p[v * 3 + 1] / weld), round(p[v * 3 + 2] / weld))

    edges: Counter = Counter()
    t = mesh.indices
    for i in range(0, len(t) - 2, 3):
        a, b, c = key(t[i]), key(t[i + 1]), key(t[i + 2])
        for e in ((a, b), (b, c), (c, a)):
            if e[0] != e[1]:
                edges[(min(e), max(e))] += 1
    return all(n % 2 == 0 for n in edges.values())


def _blank() -> list:
    return [[math.inf] * 3, [-math.inf] * 3, 0, 0.0]


def _accumulate(state: list, indices, pos, first: int, count: int) -> None:
    """Fold one draw range into ``[min, max, triangles, volume]``: per TRIANGLE, so the box comes along
    with the winding the volume needs. The volume is the signed-tetrahedron sum about the origin,
    exact for a closed mesh wherever the origin sits."""
    lo, hi = state[0], state[1]
    tris = 0
    vol = 0.0
    end = min(first + count, len(indices))
    # A count that is not a multiple of 3 is a malformed range, not a reason to read into the next.
    end -= (end - first) % 3
    for i in range(first, end, 3):
        verts = []
        for k in range(3):
            v = indices[i + k] * 3
            if v + 2 >= len(pos):
                break
            x, y, z = pos[v], pos[v + 1], pos[v + 2]
            verts.append((x, y, z))
            if x < lo[0]:
                lo[0] = x
            if y < lo[1]:
                lo[1] = y
            if z < lo[2]:
                lo[2] = z
            if x > hi[0]:
                hi[0] = x
            if y > hi[1]:
                hi[1] = y
            if z > hi[2]:
                hi[2] = z
        if len(verts) != 3:
            continue
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = verts
        vol += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        tris += 1
    state[2] += tris
    state[3] += vol / 6.0


def load(path: "pathlib.Path | str") -> dict[str, Element]:
    """``read_glb`` -> ``parse_glb`` -> ``merged_hierarchy`` -> ``elements``; empty when the GLB carries
    no ``id_hierarchy`` (nothing wrong with the file, it just cannot take part in an element-level
    comparison)."""
    js, binary = parse_glb(read_glb(path))
    hierarchy = merged_hierarchy(js)
    if hierarchy is None:
        return {}
    return elements(js, binary, hierarchy)


def write(
    meshes: "Mapping[str, tuple[Iterable[float], Iterable[int]]]",
    path: "pathlib.Path | str",
    *,
    generator: str = "ada.geom_compare",
) -> None:
    """Write one merged mesh in the layout :func:`elements` reads back -- ``{name: (flat positions,
    triangle indices)}``, everything in ONE node, identity in the draw ranges. Writing it next to the
    reader is what makes a round trip testable."""
    positions = array.array("f")
    indices = array.array("I")
    hierarchy: dict[str, list] = {}
    ranges: dict[str, list[int]] = {}
    for i, (name, (verts, tris)) in enumerate(meshes.items(), start=1):
        base = len(positions) // 3
        positions.extend(verts)
        first = len(indices)
        # Rebased onto the merged buffer: an element's own 0-based indices would draw every element
        # out of the first one's vertices -- and the file would still be valid glTF.
        indices.extend(base + int(t) for t in tris)
        count = len(indices) - first
        eid = str(i)
        hierarchy[eid] = [name, "*"]
        if count:
            ranges[eid] = [first, count]
    pos_bytes = positions.tobytes()
    pad = (-len(pos_bytes)) % 4
    binary = pos_bytes + b"\x00" * pad + indices.tobytes()
    js = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": [0], "extras": {"id_hierarchy": hierarchy, "draw_ranges_node0": ranges}}],
        "nodes": [{"name": "node0", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions) // 3,
                "type": "VEC3",
                # min/max on POSITION is REQUIRED by the spec; a viewer that culls by it shows nothing
                # when it is missing.
                "min": [min(positions[i::3], default=0.0) for i in range(3)],
                "max": [max(positions[i::3], default=0.0) for i in range(3)],
            },
            {"bufferView": 1, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes) + pad, "byteLength": len(indices) * 4, "target": 34963},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    write_glb(js, binary, path)


def write_glb(js: dict, binary: bytes, path: "pathlib.Path | str") -> None:
    """Serialise a parsed GLB back to disk, unchanged except for what was edited; both chunks padded to
    four bytes, which the spec requires."""
    js_chunk = json.dumps(js, separators=(",", ":")).encode()
    js_chunk += b" " * ((-len(js_chunk)) % 4)
    bin_chunk = binary + b"\x00" * ((-len(binary)) % 4)
    total = 12 + 8 + len(js_chunk) + 8 + len(bin_chunk)
    pathlib.Path(path).write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(js_chunk), b"JSON")
        + js_chunk
        + struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
        + bin_chunk
    )


def y_up_to_z_up(b: Bounds) -> Bounds:
    """glTF is Y-up; a Z-up model's ``(x, y, z)`` is the GLB's ``(x, -z, y)``: two axes swap and one
    flips, so that axis's min and max trade places."""
    return Bounds((b.min[0], -b.max[2], b.min[1]), (b.max[0], -b.min[2], b.max[1]))


def normalise(name: str) -> str:
    """One spelling of an element name, for the join: surrounding blanks, a leading ``/`` and case
    only. Nothing fuzzy -- turning "no geometry for this element" into "here is a different element"
    is worse than reporting the miss."""
    return name.strip().lstrip("/").strip().upper()


def index_by_name(els: Iterable[Element]) -> dict[str, Element]:
    """``normalise(name) -> Element``, folding collisions by widening."""
    out: dict[str, Element] = {}
    for el in els:
        key = normalise(el.name)
        prior = out.get(key)
        if prior is None:
            out[key] = el
            continue
        out[key] = Element(
            prior.name,
            Bounds(
                tuple(min(a, b) for a, b in zip(prior.bounds.min, el.bounds.min)),
                tuple(max(a, b) for a, b in zip(prior.bounds.max, el.bounds.max)),
            ),
            prior.triangles + el.triangles,
            prior.volume + el.volume,
        )
    return out
