"""Memory-bounded GLB assembly for the streaming STEP→GLB path.

The default scene path accumulates every placed instance's mesh in RAM, then
``concatenate_stores`` makes a 2nd full copy per material, then trimesh's
``scene.export("glb")`` holds the whole merged model *and* the GLB bytes (2-3x). On a
large assembly-placed STEP that peaks ~4.8 GB and OOMs the worker's 3.2 GB cap (the
merge alone adds ~1.8 GB; the export pushes the transient peak past 4.5 GB).

This module keeps peak RAM at ~one solid's buffers + a light manifest:

* :class:`GlbSpillStore` appends each solid's *offset-adjusted* position/index bytes to
  a per-material temp file as it streams in — the ``concatenate_stores`` concatenation,
  done incrementally to disk — keeping only running counts, the POSITION min/max, and
  the lightweight picking ``GroupReference``s in RAM.
* :func:`write_glb_from_spill` builds the glTF JSON from that manifest and streams each
  per-material temp file into the GLB BIN chunk, writing the ``.glb`` straight to disk
  without ever materialising the merged model or the GLB bytes.

The output is a spec-valid GLB that round-trips through ``trimesh.load`` and carries the
same merge-by-colour materials, ``ADA_EXT_data`` extension and ``scenes[0].extras``
picking metadata (``id_hierarchy`` + ``draw_ranges_node*``) as the trimesh export it
replaces. It deliberately omits the degenerate zeroed ``TEXCOORD_0`` trimesh emits
(no textures use it) — smaller and harmless.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import numpy as np

if TYPE_CHECKING:
    from ada.visit.colors import Color

_GLB_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_COMP_FLOAT = 5126  # FLOAT
_COMP_UINT = 5125  # UNSIGNED_INT
_MODE_TRIANGLES = 4


def _pad4(n: int) -> int:
    """Bytes of padding to round ``n`` up to a 4-byte boundary (glTF alignment)."""
    return (-n) % 4


@dataclass
class _MatSpill:
    """One material's running spill state — tiny, the only thing kept in RAM per
    material besides the open file handles and the picking ranges."""

    mat_id: int
    pos_fh: BinaryIO = field(repr=False)
    idx_fh: BinaryIO = field(repr=False)
    pos_path: str
    idx_path: str
    vert_count: int = 0  # running #vertices -> index offset + POSITION accessor count
    index_count: int = 0  # running #indices  -> draw-range starts + SCALAR accessor count
    idx_max: int = 0  # running max index value -> SCALAR accessor max
    pos_min: np.ndarray = field(default_factory=lambda: np.array([np.inf, np.inf, np.inf]))
    pos_max: np.ndarray = field(default_factory=lambda: np.array([-np.inf, -np.inf, -np.inf]))
    groups: list = field(default_factory=list, repr=False)  # GroupReference(node_ref, start, length)


class GlbSpillStore:
    """Per-material, append-only disk spill of merged mesh buffers — the incremental,
    bounded-memory replacement for ``by_material`` + ``concatenate_stores`` on the
    streaming GLB path."""

    def __init__(self, tmpdir: str | None = None):
        self._dir = tempfile.mkdtemp(prefix="ada_glb_spill_", dir=tmpdir)
        self._mats: dict[int, _MatSpill] = {}

    @property
    def tmpdir(self) -> str:
        return self._dir

    def _mat(self, mat_id: int) -> _MatSpill:
        m = self._mats.get(mat_id)
        if m is None:
            pos_path = os.path.join(self._dir, f"mat_{mat_id}.pos")
            idx_path = os.path.join(self._dir, f"mat_{mat_id}.idx")
            # noqa: SIM115 - kept open across add() calls; closed by close_writers()
            m = _MatSpill(mat_id, open(pos_path, "wb"), open(idx_path, "wb"), pos_path, idx_path)  # noqa: SIM115
            self._mats[mat_id] = m
        return m

    def add(self, mat_id: int, node_ref, pos: np.ndarray, idx: np.ndarray, normal=None) -> None:
        """Append one solid's mesh to material ``mat_id``'s spill, offset-adjusting the
        indices by the running vertex count (exactly ``optimize.concatenate_stores``).
        ``pos``/``idx`` are flat float32/uint32 buffers. ``normal`` is ignored (the GLB
        path emits no NORMAL accessor, matching trimesh's current output)."""
        from ada.visit.gltf.meshes import GroupReference

        m = self._mat(mat_id)
        pos = np.ascontiguousarray(pos, dtype="<f4")
        idx = np.ascontiguousarray(idx, dtype="<u4")
        n_verts = pos.size // 3
        # Offset indices by this material's running vertex count, then record the picking
        # range (start is in INDEX units, as in optimize.GroupReference) BEFORE bumping.
        idx_off = (idx + np.uint32(m.vert_count)).astype("<u4", copy=False)
        m.groups.append(GroupReference(node_ref, m.index_count, int(idx.size)))
        m.idx_fh.write(idx_off.tobytes())
        m.pos_fh.write(pos.tobytes())
        if idx_off.size:
            m.idx_max = max(m.idx_max, int(idx_off.max()))
        if n_verts:
            p3 = pos.reshape(-1, 3)
            m.pos_min = np.minimum(m.pos_min, p3.min(axis=0))
            m.pos_max = np.maximum(m.pos_max, p3.max(axis=0))
        m.vert_count += n_verts
        m.index_count += int(idx.size)

    def materials(self) -> list[_MatSpill]:
        """Materials in a fixed ``mat_id`` order so node/material/draw-range indices are
        stable and reproducible."""
        return [self._mats[k] for k in sorted(self._mats)]

    def close_writers(self) -> None:
        for m in self._mats.values():
            for fh in (m.pos_fh, m.idx_fh):
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass

    def cleanup(self) -> None:
        """Remove the spill temp dir. Idempotent; safe in a ``finally``."""
        self.close_writers()
        shutil.rmtree(self._dir, ignore_errors=True)


def _base_color_factor(color: Color | None) -> list[float]:
    """Reproduce ``merged_mesh_to_trimesh_scene`` + trimesh's PBR float conversion:
    ``#000000`` -> light-gray, then ``[r,g,b]/255 + [opacity]`` (the 0..1 floats trimesh
    writes via its uint8 round-trip)."""
    from ada.visit.colors import Color, color_dict

    if color is None or getattr(color, "hex", None) == "#000000":
        color = Color(*color_dict["light-gray"])
    r, g, b = color.rgb255
    return [r / 255.0, g / 255.0, b / 255.0, float(color.opacity)]


def write_glb_from_spill(
    glb_path: str | Path,
    spill: GlbSpillStore,
    color_by_mat: dict[int, Color],
    ada_ext_json: dict,
    scene_metadata: dict,
    *,
    base_frame: str = "root",
) -> None:
    """Assemble a GLB from a :class:`GlbSpillStore` and write it to ``glb_path``,
    streaming each per-material temp file into the BIN chunk so peak RAM never holds the
    merged buffers or the GLB bytes."""
    from trimesh.exchange.gltf import _jsonify

    mats = [m for m in spill.materials() if m.index_count > 0 and m.vert_count > 0]
    spill.close_writers()  # flush the append handles before we read the files back

    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes: list[dict] = []
    materials_json: list[dict] = []
    mesh_node_indices: list[int] = []
    bin_offset = 0

    for prim_i, m in enumerate(mats):
        # indices bufferView + accessor
        idx_bytes = m.index_count * 4
        bv_idx = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": bin_offset, "byteLength": idx_bytes})
        bin_offset += idx_bytes + _pad4(idx_bytes)
        acc_idx = len(accessors)
        accessors.append(
            {
                "bufferView": bv_idx,
                "componentType": _COMP_UINT,
                "count": m.index_count,
                "type": "SCALAR",
                "min": [0],
                "max": [int(m.idx_max)],
            }
        )
        # POSITION bufferView + accessor
        pos_bytes = m.vert_count * 3 * 4
        bv_pos = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": bin_offset, "byteLength": pos_bytes})
        bin_offset += pos_bytes + _pad4(pos_bytes)
        acc_pos = len(accessors)
        accessors.append(
            {
                "bufferView": bv_pos,
                "componentType": _COMP_FLOAT,
                "count": m.vert_count,
                "type": "VEC3",
                "min": [float(x) for x in m.pos_min],
                "max": [float(x) for x in m.pos_max],
            }
        )
        meshes.append(
            {
                "name": f"node{m.mat_id}",
                "primitives": [
                    {
                        "attributes": {"POSITION": acc_pos},
                        "indices": acc_idx,
                        "mode": _MODE_TRIANGLES,
                        "material": prim_i,
                    }
                ],
            }
        )
        materials_json.append(
            {
                "name": f"mat{m.mat_id}",
                "pbrMetallicRoughness": {"baseColorFactor": _base_color_factor(color_by_mat.get(m.mat_id))},
                "doubleSided": True,
            }
        )

    bin_len = bin_offset

    # Node layout matches trimesh's: root at index 0, then one node per material mesh.
    nodes: list[dict] = [{"name": base_frame, "children": []}]
    for prim_i, m in enumerate(mats):
        nodes[0]["children"].append(len(nodes))
        mesh_node_indices.append(len(nodes))
        nodes.append({"name": f"node{m.mat_id}", "mesh": prim_i})
    if not mats:
        nodes[0].pop("children")

    tree: dict = {
        "asset": {"version": "2.0", "generator": "ada-stream-glb"},
        "scene": 0,
        "scenes": [{"nodes": [0], "extras": _jsonify(scene_metadata)}],
        "nodes": nodes,
        "buffers": [{"byteLength": bin_len}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "meshes": meshes,
        "materials": materials_json,
        "extensionsUsed": ["ADA_EXT_data"],
        "extensions": {"ADA_EXT_data": ada_ext_json},
    }

    json_bytes = json.dumps(tree, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * _pad4(len(json_bytes))  # space-pad JSON chunk to 4 bytes
    json_len = len(json_bytes)
    total_len = 12 + 8 + json_len + 8 + bin_len

    with open(glb_path, "wb") as out:
        out.write(struct.pack("<III", _GLB_MAGIC, 2, total_len))
        out.write(struct.pack("<II", json_len, _CHUNK_JSON))
        out.write(json_bytes)
        out.write(struct.pack("<II", bin_len, _CHUNK_BIN))
        written = 0
        for m in mats:
            for path, nbytes in ((m.idx_path, m.index_count * 4), (m.pos_path, m.vert_count * 3 * 4)):
                with open(path, "rb") as f:
                    shutil.copyfileobj(f, out)
                pad = _pad4(nbytes)
                if pad:
                    out.write(b"\x00" * pad)
                written += nbytes + pad
        if written != bin_len:  # guard against index-offset / padding drift
            raise RuntimeError(f"glb_spill: BIN size mismatch — wrote {written}, header says {bin_len}")
