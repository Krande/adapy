"""Merge-preview utility: visualize the planned FEM-shell → plate merge partition.

The FEM→STEP/IFC writers are dominated by plate *count*; the coplanar merge is
meant to collapse edge-adjacent coplanar shells into big union plates but often
barely reduces the count. This utility runs the planned partition on the loaded
FEM source — **without building the merged CAD geometry** — colours each source
facet by its merge group, and reports the reduction it would achieve, so the
merge strategy can be tuned visually and quantitatively.

Registered as the ``merge-preview`` worker utility. It swaps between merge
algorithms and forwards their parameters (see :data:`ada.fem.formats.merge_preview.ALGORITHMS`),
so a new curved-region strategy is exposed by adding it there — no change here.

Output (viewer-ops): one ``add_overlay_geometry`` op pointing at the colorized
preview GLB the utility writes + uploads (auto-disposed ``_overlays/`` blob), a
``legend``, and a ``summary`` carrying the partition stats (raw primitives,
achieved plates, actual-vs-ideal reduction, fell-back plates, % curved).
"""

from __future__ import annotations

import pathlib
import tempfile

from ada.comms.rest.utility import utility

# Auto-disposed overlay namespace (same lifecycle bucket the diff utility uses),
# kept out of the persistent admin-managed _derived/ tree.
_OVERLAY_PREFIX = "_overlays/"

_FEM_EXTS = {".fem", ".inp", ".sif", ".sin", ".bdf", ".nas", ".med", ".rmed"}


@utility(
    name="merge-preview",
    description=(
        "Preview the FEM-shell → plate merge: colour each facet by its planned merge group "
        "and report how much the plate count would drop (no merged geometry built)."
    ),
    kwargs=[
        {
            "name": "action",
            "type": "enum",
            "default": "preview",
            "enum": ["preview", "generate"],
            "description": "preview = colorized partition overlay (no geometry built); generate = actually "
            "build the merged CAD (flat Plate + curved PlateCurved objects) and render it to a GLB for viewing.",
        },
        {
            "name": "algorithm",
            "type": "enum",
            "default": "auto",
            "enum": ["auto", "none", "coplanar", "planar", "surface", "classify", "panel"],
            "description": "Merge strategy: auto (the PRODUCTION emit — cylinder ident + curved B-spline panels "
            "+ planar merge, matches STEP/IFC), none (raw baseline), coplanar, planar (flat region growing), "
            "surface (curved→B-spline), classify (recognise planar/cylinder/freeform), panel (WIP).",
        },
        {
            "name": "mode",
            "type": "enum",
            "default": "class",
            "enum": ["class", "status", "achieved", "component"],
            "description": "Preview colouring: class (per output primitive — cylinder/curved/planar/facet, pair "
            "with algorithm=auto), status (green=merged/red=fell-back), achieved (per collapsed plate), "
            "component (per intended group).",
        },
        {
            "name": "ndigits",
            "type": "int",
            "default": 6,
            "description": "Coplanarity rounding tolerance (decimal places on normal + plane offset).",
        },
        {
            "name": "angle_tol",
            "type": "float",
            "default": 30.0,
            "description": "Max fold angle (deg) for curved-region growing (surface/panel strategies).",
        },
        {
            "name": "min_patch_quads",
            "type": "int",
            "default": 12,
            "description": "Smallest curved patch (in quads) worth fitting a surface to (surface/panel).",
        },
        {
            "name": "max_dev",
            "type": "float",
            "default": 0.0,
            "description": "Planar strategy: max distance (model units) a facet may sit off the patch "
            "plane. 0 = auto (1e-3 of the mesh bbox diagonal).",
        },
    ],
    affects=("scene.overlay",),
)
def merge_preview(
    src_path,
    *,
    storage,
    scope,
    on_progress,
    action="preview",
    algorithm="auto",
    mode="class",
    ndigits=6,
    angle_tol=30.0,
    min_patch_quads=12,
    max_dev=0.0,
    source_key=None,
    **_,
):
    import ada
    from ada.fem.formats.merge_preview import analyze_part, write_preview_glb

    src_path = pathlib.Path(src_path)
    if src_path.suffix.lower() not in _FEM_EXTS:
        raise ValueError(
            f"merge-preview needs a FEM source (shell mesh); got {src_path.suffix!r}. "
            f"Supported: {sorted(_FEM_EXTS)}"
        )

    # Name persisted overlays by the MODEL, not the worker's random temp file: the utils menu
    # filters saved overlays by loaded-model stem, so an overlay must carry the real model name.
    model = pathlib.PurePosixPath(source_key).stem if source_key else src_path.stem

    on_progress("loading-fem", 0.35)
    asm = ada.from_fem(src_path)

    if action == "generate":
        return _generate(asm, model, algorithm, storage, on_progress)

    on_progress(f"partitioning ({algorithm})", 0.55)
    res = analyze_part(
        asm,
        strategy=algorithm,
        ndigits=int(ndigits),
        angle_tol=float(angle_tol),
        min_patch_quads=int(min_patch_quads),
        max_dev=(float(max_dev) or None),  # 0 → auto
    )

    on_progress("rendering-preview", 0.8)
    glb_tmp = tempfile.mkstemp(suffix=".glb")[1]
    try:
        write_preview_glb(res, glb_tmp, mode=mode)
        overlay_key = f"{_OVERLAY_PREFIX}{model}.merge-{algorithm}-{mode}.glb"
        with open(glb_tmp, "rb") as fh:
            storage.put_bytes(overlay_key, fh.read())
    finally:
        try:
            pathlib.Path(glb_tmp).unlink()
        except OSError:
            pass

    s = res.stats
    if mode == "status":
        legend = [
            {"label": "merged (→ 1 plate)", "color": "#46b45a"},
            {"label": "fell back (unmerged)", "color": "#d23c3c"},
        ]
    elif mode == "class":
        legend = [
            {"label": "cylinder", "color": "#46be5a"},
            {"label": "curved (B-spline)", "color": "#9659dc"},
            {"label": "planar (merged)", "color": "#4678dc"},
            {"label": "facet (residual)", "color": "#d23c3c"},
        ]
    else:
        legend = [{"label": "distinct merge group", "color": "#7f7fff"}]
    on_progress("done", 1.0)
    return {
        "ops": [
            {
                "op": "add_overlay_geometry",
                "blob_key": overlay_key,
                "label": f"merge-{algorithm} ({mode})",
                "color": "#46b45a",
            }
        ],
        "legend": legend,
        "summary": s,
    }


_GEN_CLASS_COLOR = {
    "cylinder": [70, 190, 90, 255],  # green
    "curved": [150, 90, 220, 255],  # purple
    "planar": [70, 120, 220, 255],  # blue
}


def _generate(asm, model, algorithm, storage, on_progress):
    """Render the merged CAD (the SAME analytic faces the STEP/IFC 'auto' emit produces —
    cylinders + curved B-spline panels + merged flat plates) to a viewable GLB overlay.

    Tessellates via the libtess2 pipeline (adacpp NGEOM, OCC-free) instead of building tens
    of thousands of Plate objects and OCC-meshing them — that path was ~2.5x slower AND used
    the object-based merge (~44k plates on a ship) rather than the analytic engine. Faces are
    grouped by class so the GLB colours cylinders/curved/planar distinctly."""
    import numpy as np

    from ada.cad import active_backend
    from ada.fem.formats.mesh_faces import iter_fem_analytic_faces
    from ada.geom.surfaces import (
        BSplineSurfaceWithKnots,
        CylindricalSurface,
        OpenShell,
        ShellBasedSurfaceModel,
    )

    on_progress("recognising surfaces", 0.4)
    faces = list(iter_fem_analytic_faces(asm))

    def _cls(f):
        s = f.face_surface
        if isinstance(s, CylindricalSurface):
            return "cylinder"
        if isinstance(s, BSplineSurfaceWithKnots):
            return "curved"
        return "planar"

    by_cls: dict = {}
    for f in faces:
        by_cls.setdefault(_cls(f), []).append(f)
    # one shell per class → one BatchMesh group per class → colour by class
    items = [(c, ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=fs)])) for c, fs in by_cls.items()]

    on_progress("tessellating (libtess2)", 0.6)
    # This is a single whole-model call (not the per-solid STEP->GLB pool), so use all cores —
    # the curved B-spline hull panels are the cost and parallelise across the thread pool.
    import os as _os

    bm = active_backend().tessellate_stream(items, pipeline="libtess2", threads=(_os.cpu_count() or 1))

    import trimesh

    pos = np.asarray(bm.positions, dtype=np.float64).reshape(-1, 3)
    idx = np.asarray(bm.indices, dtype=np.int64).reshape(-1, 3)
    vcolors = np.full((len(pos), 4), 180, dtype=np.uint8)
    for g in bm.groups:
        vcolors[g.vstart : g.vstart + g.vlength] = _GEN_CLASS_COLOR.get(g.node_id, [180, 180, 180, 255])

    on_progress("writing-glb", 0.85)
    glb_tmp = tempfile.mkstemp(suffix=".glb")[1]
    try:
        mesh = trimesh.Trimesh(vertices=pos, faces=idx, vertex_colors=vcolors, process=False)
        trimesh.Scene(mesh).export(glb_tmp, file_type="glb")
        overlay_key = f"{_OVERLAY_PREFIX}{model}.merge-{algorithm}-generated.glb"
        with open(glb_tmp, "rb") as fh:
            storage.put_bytes(overlay_key, fh.read())
    finally:
        try:
            pathlib.Path(glb_tmp).unlink()
        except OSError:
            pass

    on_progress("done", 1.0)
    counts = {c: len(fs) for c, fs in by_cls.items()}
    return {
        "ops": [
            {
                "op": "add_overlay_geometry",
                "blob_key": overlay_key,
                "label": f"generated ({algorithm})",
                "color": "#46b45a",
            }
        ],
        "legend": [
            {"label": "cylinder", "color": "#46be5a"},
            {"label": "curved (B-spline)", "color": "#9659dc"},
            {"label": "planar (merged plate)", "color": "#4678dc"},
        ],
        "summary": {
            "action": "generate",
            "algorithm": algorithm,
            "faces": len(faces),
            "cylinder_faces": counts.get("cylinder", 0),
            "curved_faces": counts.get("curved", 0),
            "planar_faces": counts.get("planar", 0),
            "vertices": int(len(pos)),
        },
    }
