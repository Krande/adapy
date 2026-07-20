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
import os
import shutil
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


def meshopt_compress_glb(in_path: str | Path, out_path: str | Path, *, min_bytes: int = DEFAULT_MIN_BYTES) -> Path:
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
        import adacpp.cad as mo  # EXT_meshopt_compression codecs (vendored meshoptimizer in adacpp)
        import numpy as np
    except Exception:
        logger.warning("meshopt compression skipped: adacpp/numpy not available")
        return in_path

    tmp_bin = Path(out_path).with_suffix(".bintmp")
    try:
        # Stream: read only the header + JSON chunk; locate the BIN data offset and pread each
        # bufferView's source bytes from the file on demand. The whole BIN is never held in RAM (the
        # old path read the 1.5GB file + a 1.5GB copy + all encoded buffers => ~4GB peak; this bounds
        # memory to the largest single bufferView + the JSON).
        with in_path.open("rb") as f:
            magic, _ver, _len = struct.unpack("<III", f.read(12))
            if magic != _GLB_MAGIC:
                raise ValueError("not a GLB")
            jlen, jtype = struct.unpack("<II", f.read(8))
            if jtype != _CHUNK_JSON:
                raise ValueError("expected JSON chunk first")
            j = json.loads(f.read(jlen).decode("utf-8"))
            blen, btype = struct.unpack("<II", f.read(8))
            if btype != _CHUNK_BIN:
                raise ValueError("expected BIN chunk")
            bin_off = f.tell()  # file offset of the BIN data
            orig_bin_len = blen
        if "EXT_meshopt_compression" in j.get("extensionsUsed", []):
            # already meshopt-packed (e.g. the native adacpp writer did it inline) — re-encoding would
            # slice the compressed buffer as if uncompressed and corrupt it. No-op.
            logger.info("meshopt: %s already EXT_meshopt-packed; skipping", in_path.name)
            return in_path
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

        if not (attr_bv or idx_bv):
            logger.info("meshopt: no compressible bufferViews in %s; leaving uncompressed", in_path.name)
            return in_path

        # buffer 0 = [compressed regions][raw regions] (4-byte aligned), built by streaming each
        # bufferView's source from the file through the native encoder into a temp BIN file.
        comp_meta: dict[int, dict] = {}
        raw_meta: dict[int, int] = {}
        with in_path.open("rb") as fin, tmp_bin.open("wb") as fout:

            def _src(bv):
                fin.seek(bin_off + bv.get("byteOffset", 0))
                return fin.read(bv["byteLength"])

            off0 = 0

            def _emit(b):  # write + 4-align, return the start offset
                nonlocal off0
                o = off0
                fout.write(b)
                off0 += len(b)
                pad = _align4(off0) - off0
                if pad:
                    fout.write(b"\x00" * pad)
                    off0 += pad
                return o

            for i, bv in enumerate(bvs):  # compressed regions first
                if i in attr_bv:
                    a = attr_bv[i]
                    stride = bv.get("byteStride") or (_TYPE_COMPONENTS[a["type"]] * _COMP_BYTES[a["componentType"]])
                    count = bv["byteLength"] // stride
                    src = _src(bv)
                    enc = bytes(mo.meshopt_encode_vertex_buffer(src, count, stride))
                    back = bytes(mo.meshopt_decode_vertex_buffer(enc, count, stride))
                    mode = "ATTRIBUTES"
                elif i in idx_bv:
                    a = idx_bv[i]
                    stride = _COMP_BYTES[a["componentType"]]  # 2 or 4
                    count = bv["byteLength"] // stride
                    src = _src(bv)
                    idtype = np.uint16 if stride == 2 else np.uint32
                    idx = np.frombuffer(src, dtype=idtype).astype(np.uint32)
                    vcount = int(idx.max()) + 1 if count else 1
                    enc = bytes(mo.meshopt_encode_index_sequence(idx.tobytes(), count, vcount))
                    back = bytes(mo.meshopt_decode_index_sequence(enc, count, stride))
                    mode = "INDICES"
                else:
                    continue
                if back != src:
                    raise ValueError(f"bufferView {i} ({mode}) failed round-trip — aborting")
                o = _emit(enc)
                comp_meta[i] = {"byteOffset": o, "byteLength": len(enc), "stride": stride, "mode": mode, "count": count}

            for i, bv in enumerate(bvs):  # raw regions after
                if i in attr_bv or i in idx_bv:
                    continue
                raw_meta[i] = _emit(_src(bv))
            new_bin_len = off0

        fallback = 1 if len(j.get("buffers", [])) == 1 else len(j["buffers"])
        j["buffers"] = [
            {"byteLength": new_bin_len},
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

        _write_glb_streaming(out_path, j, tmp_bin, new_bin_len)
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
    finally:
        try:
            os.remove(tmp_bin)
        except OSError:
            pass


def _write_glb_streaming(out_path: Path, j: dict, bin_path: Path, bin_len: int) -> None:
    """Write the GLB as header + JSON chunk + the already-assembled (4-aligned) temp BIN file,
    copying the BIN through a bounded buffer so the full buffer is never held in RAM."""
    json_bytes = json.dumps(j, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (_align4(len(json_bytes)) - len(json_bytes))
    total = 12 + 8 + len(json_bytes) + 8 + bin_len
    with out_path.open("wb") as f:
        f.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(json_bytes), _CHUNK_JSON))
        f.write(json_bytes)
        f.write(struct.pack("<II", bin_len, _CHUNK_BIN))
        with bin_path.open("rb") as bf:
            shutil.copyfileobj(bf, f, 1024 * 1024)


if __name__ == "__main__":
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    res = meshopt_compress_glb(src, dst)
    print(json.dumps({"out": str(res), "ok": str(res) == dst}))


def meshopt_decompress_glb(in_path: str | Path, out_path: str | Path) -> Path:
    """Unpack an ``EXT_meshopt_compression`` GLB into a plain uncompressed GLB.

    The inverse of :func:`meshopt_compress_glb` for CONSUMERS that cannot decode the
    extension (trimesh's glTF loader hits ``IndexError`` on the fallback-buffer layout
    — this is what broke the audit's parity measurement of production FEM GLBs).
    Each compressed bufferView is decoded with the same codecs the packer verified
    against, raw views are copied, and everything is re-laid-out sequentially into a
    fresh BIN (accessor byteOffsets are view-relative, so a new view layout is valid).

    Raises on a missing codec or malformed input — the caller decides whether that is
    an error or a skip."""
    import adacpp.cad as mo  # EXT_meshopt_compression codecs (vendored meshoptimizer in adacpp)

    in_path = Path(in_path)
    out_path = Path(out_path)

    with in_path.open("rb") as f:
        magic, _ver, _total = struct.unpack("<III", f.read(12))
        if magic != _GLB_MAGIC:
            raise ValueError("not a GLB")
        jlen, jtype = struct.unpack("<II", f.read(8))
        if jtype != _CHUNK_JSON:
            raise ValueError("expected JSON chunk first")
        j = json.loads(f.read(jlen).decode("utf-8"))
        blen, btype = struct.unpack("<II", f.read(8))
        if btype != _CHUNK_BIN:
            raise ValueError("expected BIN chunk")
        bin_data = f.read(blen)

    if "EXT_meshopt_compression" not in j.get("extensionsUsed", []):
        raise ValueError("GLB carries no EXT_meshopt_compression")

    _MODE_DECODERS = {
        "ATTRIBUTES": lambda enc, count, stride: bytes(mo.meshopt_decode_vertex_buffer(enc, count, stride)),
        "INDICES": lambda enc, count, stride: bytes(mo.meshopt_decode_index_sequence(enc, count, stride)),
        "TRIANGLES": lambda enc, count, stride: bytes(mo.meshopt_decode_index_buffer(enc, count, stride)),
    }

    tmp_bin = out_path.with_suffix(".bintmp")
    try:
        with tmp_bin.open("wb") as fout:
            off = 0

            def _emit(b: bytes) -> int:
                nonlocal off
                o = off
                fout.write(b)
                off += len(b)
                pad = _align4(off) - off
                if pad:
                    fout.write(b"\x00" * pad)
                    off += pad
                return o

            for bv in j.get("bufferViews", []):
                ext = (bv.get("extensions") or {}).pop("EXT_meshopt_compression", None)
                if ext is not None:
                    if ext.get("filter") not in (None, "NONE"):
                        raise ValueError(f"unsupported meshopt filter {ext['filter']!r}")
                    enc = bin_data[ext["byteOffset"] : ext["byteOffset"] + ext["byteLength"]]
                    dec = _MODE_DECODERS[ext.get("mode", "ATTRIBUTES")](enc, ext["count"], ext["byteStride"])
                    if len(dec) != bv["byteLength"]:
                        raise ValueError("decoded bufferView length mismatch")
                    bv["byteOffset"] = _emit(dec)
                else:
                    start = bv.get("byteOffset", 0)
                    bv["byteOffset"] = _emit(bin_data[start : start + bv["byteLength"]])
                bv["buffer"] = 0
                if not bv.get("extensions"):
                    bv.pop("extensions", None)
            new_len = off

        j["buffers"] = [{"byteLength": new_len}]
        j["extensionsUsed"] = sorted(set(j.get("extensionsUsed", [])) - {"EXT_meshopt_compression"})
        j["extensionsRequired"] = sorted(set(j.get("extensionsRequired", [])) - {"EXT_meshopt_compression"})
        if not j["extensionsUsed"]:
            j.pop("extensionsUsed")
        if not j["extensionsRequired"]:
            j.pop("extensionsRequired")

        _write_glb_streaming(out_path, j, tmp_bin, new_len)
        return out_path
    finally:
        try:
            os.remove(tmp_bin)
        except OSError:
            pass
