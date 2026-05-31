"""Diff utility: compare the loaded scene against another branch/SHA build.

Registered as the ``diff`` worker utility. Given the currently-loaded scene GLB
and a ``compare_ref`` (branch name or commit SHA), it resolves the ref to a
published build (``versions/<branch>/<commit>/*.glb``), parses both GLBs into
per-element records, and emits a viewer-ops payload that recolours the scene.

Diff types:

* ``byCentroid`` (default) — match elements by rounded centroid. Scene-only =
  added (green); ref-only = removed (shown as a red overlay extracted from the
  ref GLB); matched = unchanged (grey).
* ``byName`` — match by element name/guid; same colour semantics.
* ``byProperty`` — for matched elements, flag MODIFIED (orange) when
  section/material/thickness differ.
* ``byCoverage`` — bin elements into ``grid_size`` cells and compare per-cell
  element **count** (and surface area) between scene and ref; colour scene
  elements by the per-cell count delta. Catches fragmentation — the same
  structure split into more/fewer pieces (the topologic-cut case) — which the
  identity-based modes miss because the pieces don't line up 1:1.

Everything here is pure-Python GLB parsing (no CAD kernel) so it runs in the
slim worker. The GLB structure produced by adapy is::

    scene.extras["id_hierarchy"]        : {node_id: [name, parent_id]}
    scene.extras["draw_ranges_node<N>"] : {node_id: [start, count]}  # into indices
    extensions.ADA_EXT_data.design_objects[*].object_guids    : {name: guid}
    extensions.ADA_EXT_data.design_objects[*].object_metadata : {name: {type, ...}}
"""
from __future__ import annotations

import io
import json
import struct
import tempfile
from dataclasses import dataclass, field

import numpy as np

from ada.comms.rest.utility import utility

# Diff colours (hex). Common semantics: green add, red remove, orange modify.
COLOR_ADDED = "#00c853"
COLOR_REMOVED = "#d50000"
COLOR_MODIFIED = "#ff9100"
COLOR_UNCHANGED = "#9e9e9e"

# glTF componentType -> numpy dtype.
_CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Element:
    name: str
    guid: str | None
    etype: str | None
    centroid: tuple[float, float, float]
    area: float  # summed triangle area (surface measure)
    node_id: str = ""  # GLB draw-range key == the frontend's rangeId (colour key)
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# GLB parsing                                                                  #
# --------------------------------------------------------------------------- #
def _parse_glb(glb_bytes: bytes) -> tuple[dict, bytes]:
    """Return (gltf_json, binary_chunk) from GLB bytes."""
    if glb_bytes[:4] != b"glTF":
        raise ValueError("not a GLB (bad magic)")
    total = struct.unpack("<I", glb_bytes[8:12])[0]
    off = 12
    gltf_json = None
    bin_chunk = b""
    while off < total:
        clen, ctype = struct.unpack("<II", glb_bytes[off : off + 8])
        cdata = glb_bytes[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:  # 'JSON'
            gltf_json = json.loads(cdata)
        elif ctype == 0x004E4942:  # 'BIN\0'
            bin_chunk = cdata
        off += 8 + clen
    if gltf_json is None:
        raise ValueError("GLB has no JSON chunk")
    return gltf_json, bin_chunk


def _read_accessor(gltf: dict, bin_chunk: bytes, accessor_idx: int) -> np.ndarray:
    acc = gltf["accessors"][accessor_idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    dtype = _CTYPE[acc["componentType"]]
    ncomp = _NCOMP[acc["type"]]
    count = acc["count"]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    n = count * ncomp
    arr = np.frombuffer(bin_chunk, dtype=dtype, count=n, offset=start)
    return arr.reshape(count, ncomp) if ncomp > 1 else arr


def _tri_area(verts: np.ndarray) -> float:
    """Summed area of a triangle soup (verts already expanded per index, Nx3,
    N divisible by 3)."""
    if len(verts) < 3:
        return 0.0
    tris = verts[: len(verts) // 3 * 3].reshape(-1, 3, 3)
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def parse_elements(glb_bytes: bytes) -> dict[str, Element]:
    """Parse a GLB into ``{element_name: Element}``.

    Centroid + area come from the per-element index range in the merged mesh;
    type/guid/metadata come from the ADA extension.
    """
    gltf, bin_chunk = _parse_glb(glb_bytes)
    scenes = gltf.get("scenes") or [{}]
    extras = scenes[0].get("extras") or {}
    id_hierarchy = extras.get("id_hierarchy") or {}

    # name/guid/metadata maps (merge across design + simulation objects).
    guids: dict[str, str] = {}
    meta: dict[str, dict] = {}
    ext = (gltf.get("extensions") or {}).get("ADA_EXT_data") or {}
    for obj in (ext.get("design_objects") or []) + (ext.get("simulation_objects") or []):
        guids.update(obj.get("object_guids") or {})
        meta.update(obj.get("object_metadata") or {})

    # node name -> (positions, indices) for every mesh-bearing node.
    nodes = gltf.get("nodes") or []
    node_geo: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for node in nodes:
        if node.get("mesh") is None:
            continue
        prim = gltf["meshes"][node["mesh"]]["primitives"][0]
        pos = _read_accessor(gltf, bin_chunk, prim["attributes"]["POSITION"])
        idx = _read_accessor(gltf, bin_chunk, prim["indices"]).astype(np.int64)
        node_geo[node.get("name", "")] = (pos, idx)

    out: dict[str, Element] = {}
    for key, ranges in extras.items():
        if not key.startswith("draw_ranges_"):
            continue
        node_name = key[len("draw_ranges_") :]  # e.g. "node0"
        geo = node_geo.get(node_name)
        if geo is None:
            continue
        pos, idx = geo
        for node_id, rng in ranges.items():
            start, count = int(rng[0]), int(rng[1])
            elem_idx = idx[start : start + count]
            if len(elem_idx) == 0:
                continue
            verts = pos[elem_idx]
            centroid = tuple(float(c) for c in verts.mean(axis=0))
            name = (id_hierarchy.get(str(node_id)) or [None])[0] or str(node_id)
            m = meta.get(name, {})
            out[name] = Element(
                name=name,
                guid=guids.get(name),
                etype=m.get("type"),
                centroid=centroid,
                area=_tri_area(verts),
                node_id=str(node_id),
                meta=m,
            )
    return out


# --------------------------------------------------------------------------- #
# Compare-ref resolution                                                       #
# --------------------------------------------------------------------------- #
def resolve_ref_glb(storage, compare_ref: str) -> bytes:
    """Resolve ``compare_ref`` to a published build GLB's bytes.

    Builds land at ``versions/<branch>/<commit>/<artefact>.glb`` (ada build
    upload). ``compare_ref`` may be:

    * a **full blob key** ``versions/<branch>/<commit>/<artefact>.glb`` — used
      verbatim (the frontend's artefact picker sends this so the diff compares
      like-for-like against the chosen GLB), or
    * a **branch name** or **commit SHA** — matched against the ``<branch>`` or
      ``<commit>`` path segment, picking the first ``.glb`` lexicographically
      (back-compat / CLI use).
    """
    ref = compare_ref.strip()
    keys = [k for k in storage.list_keys("versions/") if k.endswith(".glb")]

    if ref.startswith("versions/") and ref.endswith(".glb"):
        if ref not in keys:
            raise ValueError(f"compare build not found: {ref!r}")
        chosen = ref
    else:
        slug = ref.replace("/", "__")  # branch slug encoding
        cand = [k for k in keys if f"/{slug}/" in f"/{k}" or k.startswith(f"versions/{slug}/")]
        if not cand:
            cand = [k for k in keys if f"/{ref}/" in f"/{k}"]
        if not cand:
            raise ValueError(
                f"no published build found for ref {compare_ref!r} under versions/ "
                f"(have: {sorted(set(k.split('/')[1] for k in keys if '/' in k))[:10]})"
            )
        chosen = sorted(cand)[0]

    dest = tempfile.mkstemp(suffix=".glb")[1]
    storage.fetch_to_path(chosen, dest)
    with open(dest, "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# Diff modes                                                                   #
# --------------------------------------------------------------------------- #
def _round_key(c, nd: int) -> tuple:
    return (round(c[0], nd), round(c[1], nd), round(c[2], nd))


def _meta_signature(e: Element) -> tuple:
    m = e.meta or {}
    sec = (m.get("section") or {}).get("name")
    mat = (m.get("material") or {}).get("name")
    thk = m.get("thickness")
    return (e.etype, sec, mat, thk)


def _by_identity(scene, ref, key_fn, *, compare_props: bool = False) -> dict:
    """Shared engine for byCentroid / byName / byProperty.

    ``key_fn(Element) -> hashable`` defines the match key. With ``compare_props``
    a matched pair whose section/material/thickness differ is flagged MODIFIED.
    Returns coloured ops + legend + summary, and the removed-element names (for
    the overlay).
    """
    scene_keys = {key_fn(e): n for n, e in scene.items()}
    ref_keys = {key_fn(e): n for n, e in ref.items()}

    colors: list[dict] = []
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    removed_names: list[str] = []

    for k, name in scene_keys.items():
        nid = scene[name].node_id or name  # colour key = frontend rangeId
        if k not in ref_keys:
            colors.append({"key": nid, "color": COLOR_ADDED})
            counts["added"] += 1
        elif compare_props and _meta_signature(scene[name]) != _meta_signature(ref[ref_keys[k]]):
            colors.append({"key": nid, "color": COLOR_MODIFIED})
            counts["modified"] += 1
        else:
            colors.append({"key": nid, "color": COLOR_UNCHANGED})
            counts["unchanged"] += 1

    for k, name in ref_keys.items():
        if k not in scene_keys:
            removed_names.append(name)
            counts["removed"] += 1

    return {"colors": colors, "counts": counts, "removed_names": removed_names}


def _by_coverage(scene, ref, grid_size: float) -> dict:
    """Per-cell element-count + area delta; colour scene elements by |count Δ|.

    Conserved-area fragmentation (same structure, more pieces) shows up as a
    count delta even though area is unchanged — the signal the identity modes
    miss for the topologic cut.
    """
    def cell(c):
        return (int(c[0] // grid_size), int(c[1] // grid_size), int(c[2] // grid_size))

    def agg(elems):
        d: dict[tuple, dict] = {}
        for e in elems.values():
            b = d.setdefault(cell(e.centroid), {"count": 0, "area": 0.0})
            b["count"] += 1
            b["area"] += e.area
        return d

    sa, ra = agg(scene), agg(ref)
    deltas = {c: sa.get(c, {}).get("count", 0) - ra.get(c, {}).get("count", 0) for c in set(sa) | set(ra)}
    max_abs = max((abs(v) for v in deltas.values()), default=0) or 1

    def heat(delta):
        # 0 -> grey, +abs -> toward red (more in scene), -abs -> toward blue.
        t = max(-1.0, min(1.0, delta / max_abs))
        if abs(t) < 1e-9:
            return COLOR_UNCHANGED
        if t > 0:
            r, g, b = 255, int(200 * (1 - t)), int(200 * (1 - t))
        else:
            r, g, b = int(200 * (1 + t)), int(200 * (1 + t)), 255
        return f"#{r:02x}{g:02x}{b:02x}"

    colors = [
        {"key": e.node_id or n, "color": heat(deltas.get(cell(e.centroid), 0))}
        for n, e in scene.items()
    ]
    changed_cells = sum(1 for v in deltas.values() if v != 0)
    summary = {
        "grid_size": grid_size,
        "cells_total": len(deltas),
        "cells_changed": changed_cells,
        "scene_elements": len(scene),
        "ref_elements": len(ref),
        "net_element_delta": len(scene) - len(ref),
    }
    return {"colors": colors, "summary": summary}


# --------------------------------------------------------------------------- #
# Overlay (removed elements) extraction                                        #
# --------------------------------------------------------------------------- #
def _write_simple_glb(verts: np.ndarray, color_rgba: tuple[float, float, float, float]) -> bytes:
    """Minimal, dependency-free GLB: a triangle-soup mesh in one solid colour.

    ``verts`` is an (N, 3) float array of triangle-soup vertices (N % 3 == 0).
    Written by hand (no trimesh/scipy) so it runs in the slim worker and in
    tests — the inverse of :func:`_parse_glb`.
    """
    verts = np.ascontiguousarray(verts, dtype="<f4")
    n = len(verts)
    idx = np.arange(n, dtype="<u4")
    vbytes = verts.tobytes()
    ibytes = idx.tobytes()
    bin_blob = vbytes + ibytes  # 12-byte stride keeps the index view 4-aligned
    vmin = [float(x) for x in verts.min(axis=0)] if n else [0, 0, 0]
    vmax = [float(x) for x in verts.max(axis=0)] if n else [0, 0, 0]
    gltf = {
        "asset": {"version": "2.0", "generator": "ada-diff-overlay"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "diff_removed"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{
            "pbrMetallicRoughness": {"baseColorFactor": list(color_rgba), "metallicFactor": 0.0, "roughnessFactor": 1.0},
            "doubleSided": True,
        }],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(vbytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(vbytes), "byteLength": len(ibytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3", "min": vmin, "max": vmax},
            {"bufferView": 1, "componentType": 5125, "count": n, "type": "SCALAR"},
        ],
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_pad = bin_blob + b"\x00" * ((4 - len(bin_blob) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_pad)
    out = io.BytesIO()
    out.write(b"glTF")
    out.write(struct.pack("<II", 2, total))
    out.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
    out.write(json_bytes)
    out.write(struct.pack("<II", len(bin_pad), 0x004E4942))
    out.write(bin_pad)
    return out.getvalue()


def build_removed_overlay_glb(ref_glb: bytes, removed_names: list[str]) -> bytes | None:
    """Extract the removed elements' geometry from the ref GLB into a small,
    red-coloured overlay GLB the frontend can add to the scene.
    """
    if not removed_names:
        return None

    gltf, bin_chunk = _parse_glb(ref_glb)
    scenes = gltf.get("scenes") or [{}]
    extras = scenes[0].get("extras") or {}
    id_hierarchy = extras.get("id_hierarchy") or {}
    name_by_id = {nid: (v[0] if v else None) for nid, v in id_hierarchy.items()}
    want = set(removed_names)

    nodes = gltf.get("nodes") or []
    node_geo = {}
    for node in nodes:
        if node.get("mesh") is None:
            continue
        prim = gltf["meshes"][node["mesh"]]["primitives"][0]
        pos = _read_accessor(gltf, bin_chunk, prim["attributes"]["POSITION"])
        idx = _read_accessor(gltf, bin_chunk, prim["indices"]).astype(np.int64)
        node_geo[node.get("name", "")] = (pos, idx)

    tris = []
    for key, ranges in extras.items():
        if not key.startswith("draw_ranges_"):
            continue
        geo = node_geo.get(key[len("draw_ranges_") :])
        if geo is None:
            continue
        pos, idx = geo
        for node_id, rng in ranges.items():
            if name_by_id.get(str(node_id)) not in want:
                continue
            elem_idx = idx[int(rng[0]) : int(rng[0]) + int(rng[1])]
            tris.append(pos[elem_idx])
    if not tris:
        return None
    verts = np.concatenate(tris, axis=0)
    return _write_simple_glb(verts, (213 / 255, 0.0, 0.0, 1.0))  # COLOR_REMOVED


# --------------------------------------------------------------------------- #
# The utility                                                                  #
# --------------------------------------------------------------------------- #
@utility(
    name="diff",
    description=(
        "Compare the loaded scene against another git branch/SHA build and "
        "colour the elements to show what changed."
    ),
    kwargs=[
        {"name": "compare_ref", "type": "ref", "default": "",
         "description": "Published build (branch/commit) to compare against."},
        {"name": "diff_type", "type": "enum", "default": "byCentroid",
         "enum": ["byCentroid", "byName", "byProperty", "byCoverage"],
         "description": "How to match/compare elements between the two models."},
        {"name": "tolerance", "type": "float", "default": 0.001,
         "description": "Centroid rounding tolerance for byCentroid/byProperty."},
        {"name": "grid_size", "type": "float", "default": 1.0,
         "description": "Cell size for byCoverage region binning (model units)."},
        {"name": "show_removed_overlay", "type": "bool", "default": True,
         "description": "Add removed elements (present in ref, not in scene) as a red overlay."},
    ],
)
def diff(
    scene_glb_path,
    *,
    storage,
    scope,
    on_progress,
    compare_ref="main",
    diff_type="byCentroid",
    tolerance=0.001,
    grid_size=1.0,
    show_removed_overlay=True,
    **_,
):
    on_progress("loading-scene", 0.1)
    with open(scene_glb_path, "rb") as fh:
        scene_glb = fh.read()
    scene = parse_elements(scene_glb)

    on_progress("resolving-ref", 0.3)
    ref_glb = resolve_ref_glb(storage, compare_ref)
    ref = parse_elements(ref_glb)

    on_progress("diffing", 0.6)
    ops: list[dict] = []
    legend: list[dict] = []
    summary: dict = {"compare_ref": compare_ref, "diff_type": diff_type,
                     "scene_elements": len(scene), "ref_elements": len(ref)}

    if diff_type == "byCoverage":
        res = _by_coverage(scene, ref, grid_size)
        ops.append({"op": "color_elements", "elements": res["colors"]})
        summary.update(res["summary"])
        legend = [{"label": "more in scene", "color": "#ff0000"},
                  {"label": "equal", "color": COLOR_UNCHANGED},
                  {"label": "more in ref", "color": "#0000ff"}]
    else:
        nd = max(0, int(round(-np.log10(tolerance)))) if tolerance > 0 else 3
        compare_props = diff_type == "byProperty"
        if diff_type in ("byName", "byProperty"):
            # match by name (stable; a section change shifts the centroid).
            key_fn = lambda e: e.name  # noqa: E731
        else:  # byCentroid
            key_fn = lambda e, _nd=nd: _round_key(e.centroid, _nd)  # noqa: E731
        res = _by_identity(scene, ref, key_fn, compare_props=compare_props)
        ops.append({"op": "color_elements", "elements": res["colors"]})
        summary.update(res["counts"])
        legend = [
            {"label": "added", "color": COLOR_ADDED, "count": res["counts"]["added"]},
            {"label": "modified", "color": COLOR_MODIFIED, "count": res["counts"]["modified"]},
            {"label": "removed", "color": COLOR_REMOVED, "count": res["counts"]["removed"]},
            {"label": "unchanged", "color": COLOR_UNCHANGED, "count": res["counts"]["unchanged"]},
        ]
        if show_removed_overlay and res["removed_names"]:
            on_progress("building-overlay", 0.85)
            overlay = build_removed_overlay_glb(ref_glb, res["removed_names"])
            if overlay is not None:
                # The worker uploads viewops JSON at job.derived_key; the overlay
                # GLB rides alongside it under a deterministic sibling key.
                overlay_key = f"_derived/diff_overlays/{abs(hash((compare_ref, diff_type))) & 0xFFFFFFFF:08x}.removed.glb"
                storage.put_bytes(overlay_key, overlay)
                ops.append({"op": "add_overlay_geometry", "blob_key": overlay_key,
                            "label": "removed", "color": COLOR_REMOVED})

    on_progress("done", 1.0)
    return {"ops": ops, "legend": legend, "summary": summary}
