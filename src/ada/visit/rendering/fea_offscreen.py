"""Convenience wrappers for rendering FEA mode shapes to PNG offscreen.

The verification-report bake originally went through
`FEAResult.to_gltf(..., warp_field=..., warp_step=...)` in a loop, but
that path silently produces mode-1's deformation on every iteration
after the first — proven empirically by md5-identical per-mode posters
when invoked in a loop. The live-viewer embed never hit that bug
because it does the warp client-side, reading the bake-artefact AFBL
blobs directly.

This module exposes the embed's warp algorithm as a Python convenience
wrapper:

  * Bake the FEAResult to a temp directory via the existing
    `bake_artefacts` pipeline → produces `fea.mesh.glb` (un-deformed
    geometry) + one `fea.<field>.bin` AFBL blob per displacement
    field per step.
  * For the requested mode index (flat across `field × step` pairs),
    read the AFBL blob's float32 displacement vector and add it to
    the mesh GLB's POSITION attribute.
  * Optionally apply the Abaqus colormap on per-vertex displacement
    magnitude (matching what the live viewer paints).
  * Render via `pygfx_offscreen_utils.glb_to_image` (or chromium)
    with the existing camera-preset math.

The wrapper is intentionally single-call: each invocation bakes a
fresh temp bundle so loop state from `FEAResult.to_gltf` can't leak
between modes. Caller in a loop pays the bake cost once per mode (a
few hundred ms for the cantilever meshes); the alternative is to
expose `render_fea_mode_from_bundle` for cases where you already have
the artefacts on disk.

All non-stdlib imports are lazy inside the functions — adapy's docs
env doesn't pull `trimesh` / pygfx / chromium unless something
actually renders, so importing this module is cheap.
"""

from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    import numpy as np
    from PIL.Image import Image as PILImage

    from ada.fem.results.common import FEAResult


def _list_displacement_entries(manifest: dict) -> list[tuple[str, int]]:
    """Walk the bake manifest in field-then-step order, returning
    (blob_url, step_idx) entries for every displacement (field, step)
    pair. Matches the flat indexing the embed's
    `assembleAnimatedFeaGlb` uses — Code Aster ships N fields × 1
    step each, other solvers 1 field × N steps; the flat index gives
    a unified mode count either way."""
    out: list[tuple[str, int]] = []
    for f in manifest.get("fields", []) or []:
        cat = (f.get("category") or "").lower()
        name = (f.get("name_canonical") or "").upper()
        if not (cat == "displacement" or "DEPL" in name or name == "U"):
            continue
        blob_url = (f.get("blob") or {}).get("url")
        if not blob_url:
            continue
        for s in range(int(f.get("n_steps") or 0)):
            out.append((blob_url, s))
    return out


def _parse_afeg(buf: bytes):
    """Parse an AFEG (Adapy Field EdGe) sidecar — the bake's
    element-boundary wireframe. Format:

        bytes  content
        0..3   magic = "AFEG"
        4..7   uint32 version (=1)
        8..11  uint32 n_edges
        12..15 4-byte zero pad (header total = 16)
        16..   n_edges × (uint32 from, uint32 to)

    Returns the index array as ``(n_edges, 2)`` uint32 — same shape
    Three.js' wireframe overlay consumes via `BufferAttribute(idx, 1)`
    + `setIndex`. Returns None on a missing-or-malformed sidecar so
    the renderer falls back to triangle-only output.
    """
    import numpy as np

    if buf[:4] != b"AFEG":
        return None
    version, n_edges = struct.unpack("<II", buf[4:12])
    if version != 1 or n_edges == 0:
        return None
    expected = 16 + n_edges * 2 * 4
    if len(buf) < expected:
        return None
    return np.frombuffer(
        buf, dtype=np.uint32, count=n_edges * 2, offset=16,
    ).reshape(n_edges, 2).copy()


def _parse_afbl(buf: bytes):
    """Parse an AFBL blob into (json_header, array of shape
    (n_steps, n_points, n_components)). Lazy-imports numpy so a
    caller that never touches FEA payloads doesn't pay the cost."""
    import numpy as np

    if buf[:4] != b"AFBL":
        raise ValueError("not an AFBL blob")
    version, json_len = struct.unpack("<II", buf[4:12])
    if version != 1:
        raise ValueError(f"unsupported AFBL version {version}")
    header = json.loads(buf[12 : 12 + json_len])
    n_steps = header["n_steps"]
    n_points = header["n_points"]
    n_comp = header["n_components"]
    arr = np.frombuffer(
        buf, dtype=np.float32, count=n_steps * n_points * n_comp, offset=1024,
    ).reshape(n_steps, n_points, n_comp).copy()
    return header, arr


def _abaqus_rgba(magnitudes):
    """Per-vertex RGBA uint8 from displacement magnitudes, using the
    same blue→cyan→green→yellow→red palette the embed uses in
    `assembleFeaGlb.ts`. Normalised to the mode's max so the
    deformation hotspots peak at red."""
    import numpy as np

    if magnitudes.size == 0:
        return np.zeros((0, 4), dtype=np.uint8)
    m_max = float(magnitudes.max())
    t = magnitudes / m_max if m_max > 0 else np.zeros_like(magnitudes)
    stops = np.array([
        (0.00, 0.0, 0.0, 1.0),
        (0.25, 0.0, 1.0, 1.0),
        (0.50, 0.0, 1.0, 0.0),
        (0.75, 1.0, 1.0, 0.0),
        (1.00, 1.0, 0.0, 0.0),
    ], dtype=np.float32)
    out = np.empty((len(t), 4), dtype=np.float32)
    for i, ti in enumerate(t):
        for k in range(1, len(stops)):
            if ti <= stops[k, 0]:
                s0, s1 = stops[k - 1], stops[k]
                span = s1[0] - s0[0]
                u = (ti - s0[0]) / span if span > 0 else 0.0
                out[i, :3] = s0[1:4] + (s1[1:4] - s0[1:4]) * u
                break
        else:
            out[i, :3] = stops[-1, 1:4]
    out[:, 3] = 1.0
    return (out * 255.0).clip(0, 255).astype(np.uint8)


def render_fea_mode_from_bundle(
    case_dir: Path,
    mode_index: int = 0,
    *,
    apply_colormap: bool = True,
    backend: Literal["pygfx", "chromium"] = "pygfx",
    preset: Optional[dict] = None,
    size: tuple[int, int] = (640, 480),
) -> "PILImage":
    """Render a single deformed mode shape from a pre-baked FEA
    artefact bundle (the layout `bake_artefacts` writes).

    `case_dir` must contain `fea.mesh.glb`, `fea.manifest.json`, and
    the AFBL displacement blobs the manifest references.

    All heavy imports (numpy, trimesh, pygfx, playwright) are lazy
    inside the function — pygltflib intentionally is NOT used, so
    callers in slimmer envs (e.g. the docs build) don't need it.
    """
    import numpy as np
    import trimesh
    import trimesh.visual.color as _tvc

    case_dir = Path(case_dir)
    mesh_glb = case_dir / "fea.mesh.glb"
    manifest_path = case_dir / "fea.manifest.json"
    if not mesh_glb.is_file():
        raise FileNotFoundError(f"missing {mesh_glb}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = _list_displacement_entries(manifest)
    if not entries:
        raise ValueError(f"no displacement fields in {manifest_path}")
    if mode_index < 0 or mode_index >= len(entries):
        raise IndexError(
            f"mode_index {mode_index} out of range (have {len(entries)} modes)"
        )

    blob_url, step_idx = entries[mode_index]
    header, steps = _parse_afbl((case_dir / blob_url).read_bytes())

    # Load via trimesh — handles GLB binary surgery + COLOR_0 write-
    # back without us hand-rolling pygltflib accessors. `force="scene"`
    # keeps the node hierarchy + multi-mesh case correct.
    scene = trimesh.load(str(mesh_glb), force="scene")
    if not scene.geometry:
        raise ValueError(f"mesh GLB {mesh_glb} contained no geometry")
    geom_key = next(iter(scene.geometry))
    mesh = scene.geometry[geom_key]
    base_positions = np.asarray(mesh.vertices, dtype=np.float32).copy()
    n_verts = base_positions.shape[0]
    if header["n_points"] != n_verts:
        raise ValueError(
            f"vertex count mismatch: mesh has {n_verts}, field has {header['n_points']}"
        )

    # Same warp logic the embed's assembleAnimatedFeaGlb runs:
    # delta = step[:, :3], position = base + delta. Scale 1.0 —
    # amplification belongs in a user-facing control, not the renderer.
    delta = steps[step_idx, :, :3].astype(np.float32)
    mesh.vertices = base_positions + delta

    if apply_colormap:
        mag = np.linalg.norm(delta, axis=1)
        rgba = _abaqus_rgba(mag)
        if isinstance(mesh, trimesh.PointCloud):
            # PointCloud carries its colours on the `.colors` attribute
            # — `colors` (not `vertex_colors`); trimesh writes them
            # straight into COLOR_0 on export.
            mesh.colors = rgba
        else:
            # Trimesh: assigning to `mesh.visual.vertex_colors` keeps
            # the existing `TextureVisuals` (from the bake's `mat0`
            # PBR material), which silently drops vertex colours on
            # GLB export — the result is a colorless POSITION-only
            # primitive. Replace the whole visual with a
            # `ColorVisuals` so `visual.kind == "vertex"` and the
            # exporter emits COLOR_0 alongside POSITION.
            mesh.visual = _tvc.ColorVisuals(mesh=mesh, vertex_colors=rgba)

    # Element-edge wireframe overlay. The bake writes
    # `fea.mesh.edges.bin` (AFEG) — deduped uint32 (from, to) pairs
    # sharing the mesh's vertex layout. Add a `trimesh.PointCloud`
    # parented separately? No — trimesh exports `Path3D` as glTF
    # LINES which is what pygfx_offscreen_utils' mode=1 branch
    # renders. Skipped silently when the sidecar is missing or the
    # mesh was loaded as a PointCloud (the bake doesn't ship edges
    # for those cases yet).
    edges_bin = case_dir / "fea.mesh.edges.bin"
    if edges_bin.is_file() and not isinstance(mesh, trimesh.PointCloud):
        try:
            edge_indices = _parse_afeg(edges_bin.read_bytes())
            if edge_indices is not None and edge_indices.size > 0:
                # `Path3D` consumes lists of entities + a shared vertex
                # array. Use `Line` entities with two-point segments
                # so the export emits one GL LINES primitive per pair.
                from trimesh.path.entities import Line
                from trimesh.path.path import Path3D

                deformed_verts = np.asarray(mesh.vertices, dtype=np.float32)
                line_entities = [Line(points=pair.tolist()) for pair in edge_indices]
                edge_path = Path3D(entities=line_entities, vertices=deformed_verts)
                scene.add_geometry(edge_path, geom_name="edges")
        except Exception as exc:
            # Wireframe is decorative — log + fall through to mesh-only.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "fea_offscreen: edge wireframe load failed (%s); rendering mesh only", exc,
            )

    with tempfile.TemporaryDirectory(prefix="fea_render_") as tmp:
        out_glb = Path(tmp) / "deformed.glb"
        scene.export(file_obj=str(out_glb), file_type="glb")

        if backend == "chromium":
            from ada.visit.rendering.chromium_offscreen_utils import (
                glb_to_image_via_browser,
            )
            return glb_to_image_via_browser(out_glb, preset=preset, size=size)
        from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image

        # Translate paradoc-style preset dict into glb_to_image kwargs.
        allowed = {
            "azimuth_deg", "elevation_deg", "fov_deg", "distance",
            "margin", "z_up",
        }
        kwargs = {k: v for k, v in (preset or {}).items() if k in allowed}
        return glb_to_image(out_glb, size=size, **kwargs)


def render_fea_mode(
    res: "FEAResult",
    mode_index: int = 0,
    *,
    apply_colormap: bool = True,
    backend: Literal["pygfx", "chromium"] = "pygfx",
    preset: Optional[dict] = None,
    size: tuple[int, int] = (640, 480),
) -> "PILImage":
    """Render a single deformed mode shape from a live :class:`FEAResult`.

    Bakes the FEAResult to a temporary artefact bundle via
    `bake_artefacts`, then warps + renders one mode via
    :func:`render_fea_mode_from_bundle`. The temp directory is wiped
    on return.

    `mode_index` is 0-based and flat across all displacement
    `(field, step)` pairs — for Code Aster this maps to the N-th
    eigenmode regardless of which `result__DEPL[N]` field carries it;
    for Calculix/Abaqus/Sesam it's the N-th step inside the single
    `U` field. The embed's `assembleAnimatedFeaGlb` uses the same
    flattening so live viewer + offscreen poster index modes
    identically.

    For tight loops over modes prefer calling `bake_artefacts`
    once and feeding `case_dir` to
    :func:`render_fea_mode_from_bundle` directly — that avoids
    re-baking the same mesh + field blobs per call.
    """
    from ada.fem.results.artefacts import FEAResultStreamAdapter, bake_artefacts

    with tempfile.TemporaryDirectory(prefix="fea_render_bundle_") as tmp:
        case_dir = Path(tmp)
        reader = FEAResultStreamAdapter(res)
        bake_artefacts(reader, case_dir, src=res.name or "fea")
        return render_fea_mode_from_bundle(
            case_dir,
            mode_index=mode_index,
            apply_colormap=apply_colormap,
            backend=backend,
            preset=preset,
            size=size,
        )
