"""Per-conversion triangle tallies, folded into the audit ``convert_meta`` so a
run-to-run change in tessellation output (a regression, a density-toggle drift, a
silently-dropped solid) is visible without re-downloading and re-parsing the artefact.

Same mechanism as the OCC mesh-health / tess-fallback tallies (``ada.occ.tessellating``):
the conversion records a compact stats dict here; ``subprocess_convert`` consumes it at
job completion and emits a ``[TRISTATS-JSON]`` marker line the worker parent folds into
``audit_log.convert_meta["tri_stats"]``.

Schema (all keys optional except ``n_tris``):
    {
      "n_tris": <int>,                # total output triangles
      "engine": "adacpp:libtess2" | "adacpp:occ" | ...,
      "n_primitives": <int>,          # GLB: draw primitives (material-merged, not solids)
      "n_solids": <int>,              # mesh/GLB: source solids/products when known
      "max_tris_per_solid": <int>,    # heaviest single solid, when known
    }
The primary regression signal is ``n_tris`` (e.g. the adaptive-density thread bug that
doubled a monster solid's triangles would show up here immediately); ``n_solids`` catches
"no geometry left behind" drops.
"""

from __future__ import annotations

import json
import struct
import threading

_LOCK = threading.Lock()
_STATS: dict = {}


def record_tri_stats(
    *,
    n_tris: int,
    engine: str | None = None,
    n_solids: int | None = None,
    n_primitives: int | None = None,
    max_tris_per_solid: int | None = None,
) -> None:
    """Record this conversion's triangle tally (last writer wins — one conversion per child)."""
    with _LOCK:
        _STATS.clear()
        _STATS["n_tris"] = int(n_tris)
        if engine:
            _STATS["engine"] = str(engine)
        if n_solids is not None:
            _STATS["n_solids"] = int(n_solids)
        if n_primitives is not None:
            _STATS["n_primitives"] = int(n_primitives)
        if max_tris_per_solid is not None:
            _STATS["max_tris_per_solid"] = int(max_tris_per_solid)


def consume_tri_stats() -> dict:
    """Return and clear the recorded stats (empty dict if none recorded)."""
    with _LOCK:
        s = dict(_STATS)
        _STATS.clear()
        return s


# Triangle-primitive modes in glTF (4=TRIANGLES, 5=TRIANGLE_STRIP, 6=TRIANGLE_FAN). Default is 4.
_TRI_MODES = {4, 5, 6}


def count_glb_tri_stats(path) -> dict:
    """Total triangles + primitive count of a .glb, parsing ONLY the JSON chunk (no vertex data).

    adacpp merges solids by material, so a GLB primitive is a material batch, not a source solid —
    hence ``n_primitives`` (not ``n_solids``). Returns ``{}`` on any parse failure (best-effort).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"glTF":
                return {}
            # First chunk after the 12-byte header must be the JSON chunk.
            chunk_hdr = f.read(8)
            if len(chunk_hdr) < 8:
                return {}
            clen, ctype = struct.unpack("<II", chunk_hdr)
            if ctype != 0x4E4F534A:  # 'JSON'
                return {}
            gltf = json.loads(f.read(clen).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {}

    accessors = gltf.get("accessors") or []
    n_tris = 0
    n_prims = 0
    for mesh in gltf.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            if prim.get("mode", 4) not in _TRI_MODES:
                continue
            idx = prim.get("indices")
            attrs = prim.get("attributes") or {}
            acc = None
            if idx is not None and 0 <= idx < len(accessors):
                acc = accessors[idx]
            elif "POSITION" in attrs and 0 <= attrs["POSITION"] < len(accessors):
                acc = accessors[attrs["POSITION"]]
            if acc is None:
                continue
            n_prims += 1
            n_tris += int(acc.get("count", 0)) // 3
    if n_tris <= 0:
        return {}
    return {"n_tris": n_tris, "n_primitives": n_prims}
