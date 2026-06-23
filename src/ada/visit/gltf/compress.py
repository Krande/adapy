"""Optional post-process GLB compression via ``gltfpack`` (meshopt).

Runs on the finished GLB just before upload, so a single hook covers every
GLB-producing path (trimesh export, the streaming spill writer, step2glb).
Off by default; selected per job through the ``glb_compression`` conversion
option:

    off       — no-op (default)
    quantize  — KHR_mesh_quantization only (int16/int8 attributes). Shrinks
                the file AND the GPU upload / VRAM (~2-4x fewer bytes per
                vertex). No client decoder needed — three.js dequantizes on
                read.
    meshopt   — EXT_meshopt_compression + quantization (gltfpack -cc).
                Smallest download; the client meshopt decoder expands it
                back to the quantized bytes (so upload/VRAM match 'quantize',
                download is smaller still).

Decoder support on the viewer is always on (GLTFLoader.setMeshoptDecoder),
so a compressed GLB loads wherever an uncompressed one does.

REQUIREMENTS / CAVEATS
----------------------
* Needs the ``gltfpack`` binary on PATH in the worker image. When absent
  this is a SAFE NO-OP: it logs once and returns the input unchanged — the
  toggle never fails a conversion.
* gltfpack rewrites the glTF. We pass ``-kn`` (keep node names) and ``-ke``
  (keep extras) so per-node draw-range names and scene extras survive, but
  gltfpack can still drop UNKNOWN top-level extensions (e.g. ADA_EXT_data)
  and may merge nodes. Validate the viewer's picking / hierarchy /
  simulation metadata on a representative model before enabling for
  ada-authored GLBs. Geometry-only (debug/diff) GLBs are the safe first
  target.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_MODES = ("off", "quantize", "meshopt")

_warned_missing = False


def _gltfpack() -> str | None:
    return shutil.which("gltfpack")


def normalize_mode(mode: str | None) -> str:
    m = (mode or "off").strip().lower()
    return m if m in VALID_MODES else "off"


def compress_glb(in_path: str | Path, mode: str | None, *, timeout_s: int = 900) -> Path:
    """Return a path to the (possibly compressed) GLB.

    On ``off`` / missing binary / any gltfpack failure, returns ``in_path``
    unchanged so the caller always has a valid file to upload. When it does
    compress, the result is written next to the input as ``*.pack.glb`` and
    that new path is returned (caller is responsible for cleanup).
    """
    global _warned_missing
    in_path = Path(in_path)
    m = normalize_mode(mode)
    if m == "off":
        return in_path

    exe = _gltfpack()
    if exe is None:
        if not _warned_missing:
            logger.warning(
                "glb_compression=%s requested but 'gltfpack' is not on PATH; "
                "uploading uncompressed. Install gltfpack in the worker image to enable.",
                m,
            )
            _warned_missing = True
        return in_path

    out_path = in_path.with_suffix(".pack.glb")
    # -kn keep node names, -ke keep extras (preserve viewer picking/tree
    # metadata as far as gltfpack allows). -cc adds EXT_meshopt_compression
    # on top of the default quantization; without it gltfpack still
    # quantizes (KHR_mesh_quantization) which is the upload/VRAM win.
    args = [exe, "-i", str(in_path), "-o", str(out_path), "-kn", "-ke"]
    if m == "meshopt":
        args.append("-cc")

    try:
        proc = subprocess.run(
            args,
            timeout=timeout_s,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("gltfpack failed (%s); uploading uncompressed", exc)
        _safe_unlink(out_path)
        return in_path

    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        logger.warning(
            "gltfpack returned %s / no output; uploading uncompressed. tail: %s",
            proc.returncode,
            (proc.stdout or b"")[-300:],
        )
        _safe_unlink(out_path)
        return in_path

    try:
        before = in_path.stat().st_size
        after = out_path.stat().st_size
        logger.info(
            "glb_compression=%s: %.1f MB -> %.1f MB (%.0f%%) %s",
            m,
            before / 1e6,
            after / 1e6,
            (after / before * 100) if before else 100.0,
            in_path.name,
        )
    except OSError:
        pass
    return out_path


def _safe_unlink(p: Path) -> None:
    try:
        if p.is_file():
            os.unlink(p)
    except OSError:
        pass
