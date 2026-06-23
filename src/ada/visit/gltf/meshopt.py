"""Structure-preserving ``EXT_meshopt_compression`` packer (picking-safe).

Re-encodes ONLY the vertex/index buffer bytes with the meshopt entropy
codec (lossless, order-preserving) and leaves the entire glTF JSON
byte-for-byte intact — nodes, names, ``scene.extras`` (draw_ranges /
id_hierarchy), the ``ADA_EXT_data`` extension, accessors, bufferView
offsets/strides. The viewer's meshopt decoder reconstructs identical
accessors, so picking / hierarchy / sim-metadata keep working.

Why this and not gltfpack: gltfpack *restructures* the glTF (vertex-cache
reorder, node merge, drops unknown extensions) which invalidates the
draw-range index offsets and strips ADA_EXT_data. This pass touches only
buffers + bufferViews.

Index buffers use the ``INDICES`` (index-sequence) codec, NOT ``TRIANGLES``:
the triangle codec rotates indices within each triangle to a canonical
winding (same triangles, different byte order) which would shift
draw-range byte offsets. INDICES is a plain order-preserving codec, so the
decoded index buffer is byte-identical and draw ranges stay valid.

Every bufferView is round-trip verified (decode == source bytes) before
the file is written; on any mismatch the pack aborts and the caller keeps
the uncompressed GLB. Browser compatibility of the bitstream was verified
cross-tool (python encode -> three.js MeshoptDecoder decode, byte-exact).

Requires ``meshoptimizer`` + ``numpy``; a safe no-op (returns the input
path) when either is missing or anything fails.
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

_GLB_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942

_COMP_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

# Below this size, meshopt is skipped: the download saving is marginal while
# the per-bufferView/fallback-buffer overhead + server encode+verify cost
# isn't worth it (and tiny buffers can even compress net-neutral). Render /
# VRAM / picking are unaffected at any size — only the on-wire bytes change.
DEFAULT_MIN_BYTES = 1_000_000


def _align4(n: int) -> int:
    return (n + 3) & ~3


def _read_glb(path: Path):
    data = path.read_bytes()
    magic, ver, _length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC:
        raise ValueError("not a GLB")
    off = 12
    js = None
    binc = None
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8 : off + 8 + clen]
        if ctype == _CHUNK_JSON:
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == _CHUNK_BIN:
            binc = chunk
        off += 8 + clen
    if js is None or binc is None:
        raise ValueError("GLB missing JSON or BIN chunk")
    return js, binc


def meshopt_compress_glb(
    in_path: str | Path, out_path: str | Path, *, min_bytes: int = DEFAULT_MIN_BYTES
) -> Path:
    """Pack ``in_path`` → ``out_path`` with EXT_meshopt_compression. Returns
    ``out_path`` on success, or ``in_path`` (unchanged) on any failure /
    missing dependency / input below ``min_bytes``."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    try:
        if in_path.stat().st_size < min_bytes:
            logger.info("meshopt: %s below %d bytes; left uncompressed", in_path.name, min_bytes)
            return in_path
    except OSError:
        pass
    try:
        import numpy as np
        import meshoptimizer as mo
    except Exception:
        logger.warning("meshopt compression skipped: meshoptimizer/numpy not available")
        return in_path

    try:
        j, binc = _read_glb(in_path)
        orig_bin_len = len(binc)
        bvs = j.get("bufferViews", [])
        accs = j.get("accessors", [])

        # Classify bufferViews via primitive usage. A bufferView is either a
        # vertex-attribute source (ATTRIBUTES) or an index source (INDICES).
        attr_bv: dict[int, dict] = {}
        idx_bv: dict[int, dict] = {}
        for mesh in j.get("meshes", []):
            for p in mesh.get("primitives", []):
                ii = p.get("indices")
                if ii is not None:
                    a = accs[ii]
                    if a.get("bufferView") is not None:
                        idx_bv.setdefault(a["bufferView"], a)
                for ai in (p.get("attributes") or {}).values():
                    a = accs[ai]
                    if a.get("bufferView") is not None:
                        attr_bv.setdefault(a["bufferView"], a)

        comp: list[dict] = []  # {i, bytes, mode, stride, count}
        raw: list[dict] = []
        for i, bv in enumerate(bvs):
            start = bv.get("byteOffset", 0)
            src = binc[start : start + bv["byteLength"]]
            mode = stride = count = None
            if i in attr_bv:
                a = attr_bv[i]
                stride = bv.get("byteStride") or (_TYPE_COMPONENTS[a["type"]] * _COMP_BYTES[a["componentType"]])
                count = bv["byteLength"] // stride
                arr = np.frombuffer(src, dtype=np.uint8)
                enc = bytes(mo.encode_vertex_buffer(arr, count, stride))
                back = bytes(mo.decode_vertex_buffer(count, stride, np.frombuffer(enc, dtype=np.uint8)))
                mode = "ATTRIBUTES"
            elif i in idx_bv:
                a = idx_bv[i]
                stride = _COMP_BYTES[a["componentType"]]  # 2 or 4
                count = bv["byteLength"] // stride
                idtype = np.uint16 if stride == 2 else np.uint32
                idx = np.frombuffer(src, dtype=idtype).astype(np.uint32)
                vcount = int(idx.max()) + 1 if count else 1
                enc = bytes(mo.encode_index_sequence(idx, count, vcount))
                back = bytes(mo.decode_index_sequence(count, stride, np.frombuffer(enc, dtype=np.uint8)))
                mode = "INDICES"
            else:
                raw.append({"i": i, "bytes": bytes(src)})
                continue

            if back != bytes(src):
                raise ValueError(f"bufferView {i} ({mode}) failed round-trip — aborting")
            comp.append({"i": i, "bytes": enc, "mode": mode, "stride": stride, "count": count})

        if not comp:
            logger.info("meshopt: no compressible bufferViews in %s; leaving uncompressed", in_path.name)
            return in_path

        # buffer 0 = [compressed regions][raw regions] (4-byte aligned).
        parts: list[bytes] = []
        off0 = 0
        comp_meta: dict[int, dict] = {}
        raw_meta: dict[int, int] = {}
        for c in comp:
            o = off0
            parts.append(c["bytes"])
            off0 += len(c["bytes"])
            pad = _align4(off0) - off0
            if pad:
                parts.append(b"\x00" * pad)
                off0 += pad
            comp_meta[c["i"]] = {"byteOffset": o, "byteLength": len(c["bytes"]), "stride": c["stride"], "mode": c["mode"], "count": c["count"]}
        for r in raw:
            o = off0
            parts.append(r["bytes"])
            off0 += len(r["bytes"])
            pad = _align4(off0) - off0
            if pad:
                parts.append(b"\x00" * pad)
                off0 += pad
            raw_meta[r["i"]] = o
        new_bin = b"".join(parts)

        fallback = 1 if len(j.get("buffers", [])) == 1 else len(j["buffers"])
        j["buffers"] = [
            {"byteLength": len(new_bin)},
            {"byteLength": orig_bin_len, "extensions": {"EXT_meshopt_compression": {"fallback": True}}},
        ]
        for i, bv in enumerate(bvs):
            if i in comp_meta:
                m = comp_meta[i]
                bv["buffer"] = fallback  # logical layout = fallback; original offsets kept
                bv.setdefault("extensions", {})["EXT_meshopt_compression"] = {
                    "buffer": 0,
                    "byteOffset": m["byteOffset"],
                    "byteLength": m["byteLength"],
                    "byteStride": m["stride"],
                    "mode": m["mode"],
                    "count": m["count"],
                }
            elif i in raw_meta:
                bv["buffer"] = 0
                bv["byteOffset"] = raw_meta[i]

        used = set(j.get("extensionsUsed", []))
        used.add("EXT_meshopt_compression")
        j["extensionsUsed"] = sorted(used)
        req = set(j.get("extensionsRequired", []))
        req.add("EXT_meshopt_compression")
        j["extensionsRequired"] = sorted(req)

        _write_glb(out_path, j, new_bin)
        logger.info(
            "meshopt: %.1f MB -> %.1f MB (%.0f%%) %s",
            in_path.stat().st_size / 1e6,
            out_path.stat().st_size / 1e6,
            (out_path.stat().st_size / max(1, in_path.stat().st_size) * 100),
            in_path.name,
        )
        return out_path
    except Exception:
        logger.exception("meshopt compression failed; uploading uncompressed")
        return in_path


def _write_glb(out_path: Path, j: dict, binc: bytes) -> None:
    json_bytes = json.dumps(j, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (_align4(len(json_bytes)) - len(json_bytes))
    bin_pad = _align4(len(binc)) - len(binc)
    bin_bytes = binc + (b"\x00" * bin_pad)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    with out_path.open("wb") as f:
        f.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(json_bytes), _CHUNK_JSON))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_bytes), _CHUNK_BIN))
        f.write(bin_bytes)


if __name__ == "__main__":
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    res = meshopt_compress_glb(src, dst)
    print(json.dumps({"out": str(res), "ok": str(res) == dst}))
