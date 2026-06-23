"""Optional post-process GLB compression, applied just before upload so a
single hook covers every GLB-producing path (trimesh export, the streaming
spill writer, step2glb).

Off by default; selected per job via the ``glb_compression`` conversion
option:

    off      — no-op (default)
    meshopt  — structure-preserving EXT_meshopt_compression (see
               ``meshopt.py``). Re-encodes only the vertex/index buffer
               bytes (lossless, order-preserving) and leaves the glTF JSON
               byte-identical, so node names, scene.extras draw_ranges,
               id_hierarchy and the ADA_EXT_data extension survive and the
               viewer's picking / hierarchy keep working. ~2.5-3x smaller
               download; decoded client-side by GLTFLoader.setMeshoptDecoder.

Fully guarded: any failure (or a missing meshoptimizer/numpy) uploads the
original GLB unchanged — the toggle is never a hard failure.

NOTE: this intentionally does NOT use gltfpack. gltfpack restructures the
glTF (vertex-cache reorder + node merge + drops unknown extensions), which
invalidates draw-range index offsets and strips ADA_EXT_data — i.e. it
breaks picking. The bufferView-level meshopt pass here preserves all of it.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_MODES = ("off", "meshopt")


def normalize_mode(mode: str | None) -> str:
    m = (mode or "off").strip().lower()
    # Back-compat: an old "quantize" request maps to the safe meshopt pass
    # (quantization is not yet wired; meshopt alone is the picking-safe win).
    if m == "quantize":
        m = "meshopt"
    return m if m in VALID_MODES else "off"


def compress_glb(in_path: str | Path, mode: str | None) -> Path:
    """Return a path to the (possibly compressed) GLB. On ``off`` / any
    failure, returns ``in_path`` unchanged. On success, writes ``*.pack.glb``
    next to the input and returns that path (caller cleans it up)."""
    in_path = Path(in_path)
    m = normalize_mode(mode)
    if m != "meshopt":
        return in_path
    from .meshopt import meshopt_compress_glb

    out_path = in_path.with_suffix(".pack.glb")
    return meshopt_compress_glb(in_path, out_path)
