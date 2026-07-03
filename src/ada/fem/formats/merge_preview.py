"""Merge-strategy preview + diagnostics for the FEM-shell → CAD plate merge.

The FEM→STEP/IFC writers are dominated by plate *count*: the coplanar merge
(:mod:`ada.fem.formats.mesh_faces`) is supposed to collapse edge-adjacent
coplanar shells into big union plates, but in practice the reduction is often
far smaller than it should be. This module answers *why* — without building any
merged geometry — by computing the planned partition and reporting where it
fragments, plus emitting a colorized GLB so the fragmentation is visible.

Two labels per primitive face (a coplanar quad / triangle / warped-quad-half):

* **component** — the intended merge group: a plane bucket (material + thickness
  + canonical normal + offset) split into edge-connected components. This is what
  *should* collapse to one plate.
* **achieved** — what actually collapses: the component's id when the union merges
  to one clean simple loop, else each primitive is its own achieved group (the
  all-or-nothing fallback in :func:`_merge_component`).

The gap between them is the story:

* ``components ≈ primitives``   → the *partition* is too fine (bucket/adjacency
  tolerance) — the region growing never groups enough. → algorithm/tolerance work.
* ``components ≪ primitives`` but ``achieved ≈ primitives`` → the partition is
  fine but the all-or-nothing merge *fallback* discards big groups. → robust
  boundary extraction (holes, partial merges).
* many primitives are warped-quad halves that never merge → curved skin needs a
  B-spline surface fit (which adacpp currently lacks). → curved-merge work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ada.fem.formats.mesh_faces import (
    _auto_max_dev,
    _block_primitives,
    _canonical_sign,
    _planar_patches,
    _shell_blocks,
    _surface_patches,
    classify_patch,
)


@dataclass
class PartitionResult:
    """The planned partition of one assembly's shell primitives + its stats."""

    outlines: list = field(default_factory=list)  # (k,3) float arrays, per primitive
    component: list = field(default_factory=list)  # intended merge-group id per primitive
    achieved: list = field(default_factory=list)  # actually-collapsed group id per primitive
    is_split_tri: list = field(default_factory=list)  # primitive came from a warped-quad split (curved hint)
    patch_class: list = field(
        default_factory=list
    )  # fitted primitive per primitive (classify mode): planar|cylinder|freeform|small|""
    stats: dict = field(default_factory=dict)


def _component_labels(prims, idxs, ndigits):
    """Edge-connected components within one plane bucket (union-find on shared
    node edges) — mirrors ``_merge_plane_bucket``. Returns list[list[prim_idx]]."""
    if len(idxs) == 1:
        return [[idxs[0]]]
    parent = list(range(len(idxs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edge_owner: dict = {}
    for li, j in enumerate(idxs):
        r = prims.rows[j]
        k = len(r)
        for e in range(k):
            a, b = r[e], r[(e + 1) % k]
            ek = (a, b) if a <= b else (b, a)
            if ek in edge_owner:
                union(li, edge_owner[ek])
            else:
                edge_owner[ek] = li

    comps: dict = {}
    for li in range(len(idxs)):
        comps.setdefault(find(li), []).append(idxs[li])
    return list(comps.values())


def _merge_succeeds(prims, cj, ndigits) -> bool:
    """True if this multi-primitive component collapses to ONE clean simple loop —
    the exact success test :func:`_merge_component` uses before it merges."""
    from ada.core.vector_utils import (
        merge_coplanar_loops_by_edge_cancellation,
        project_points_to_local_2d,
    )
    from ada.fem.formats.concept_merge import _loop_is_simple_2d

    loops = [prims.outline(j) for j in cj]
    merged = merge_coplanar_loops_by_edge_cancellation(loops, ndigits=ndigits)
    if merged is None:
        return False
    try:
        pts2d, _ = project_points_to_local_2d(merged)
    except Exception:  # noqa: BLE001
        return False
    return bool(_loop_is_simple_2d(pts2d))


# Algorithms the preview can partition by. Each maps to a grouping of the
# per-block primitives; new strategies (curved-surface / structured-panel region
# growing) slot in here so the utility can swap between them at runtime.
ALGORITHMS = ("none", "coplanar", "planar", "surface", "classify", "auto", "panel")


def _plane_buckets(prims, ndigits, idxs=None):
    """(material, thickness, canonical-normal, offset) buckets — mirrors
    ``_coplanar_block`` / ``_plate_plane_key``. ``idxs`` restricts the bucketing to a
    subset of primitives (used for the surface strategy's small-patch fallback).
    Returns list[list[prim_idx]]."""
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    p0 = prims.coords[np.array([r[0] for r in prims.rows], dtype=np.int64)]
    offset = np.round(sign * np.sum(normals * p0, axis=1), ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)
    buckets: dict = {}
    for j in range(len(prims)) if idxs is None else idxs:
        key = (prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), float(offset[j]))
        buckets.setdefault(key, []).append(j)
    return list(buckets.values())


def _strategy_groups(prims, strategy, ndigits, angle_tol, min_patch_quads, max_dev, blk=None):
    """Partition one block's primitives into ``[(prim_indices, kind, cls), ...]``.

    ``kind`` drives the achieved-merge test in :func:`analyze_part`: ``single`` (1
    primitive, trivially its own plate), ``coplanar`` (collapses only if the union is
    one clean simple loop — used for planar patches too), ``merged``/``surface`` (a
    region-grown patch that is one face). ``cls`` is the fitted primitive class for the
    ``classify``/``auto`` strategies (planar|cylinder|freeform|curved|facet|small), else "".
    """
    n = len(prims)
    if strategy == "none":
        return [([j], "single", "") for j in range(n)]

    if strategy == "auto":
        # Mirror the PRODUCTION analytic emit (mesh_faces.iter_fem_analytic_faces): cylinder
        # recognition + cross-T curved B-spline panels + planar whole-patch merge + facet
        # residual, so the colorized preview matches what STEP/IFC/generate actually build.
        from ada.fem.formats.mesh_faces import (
            _elid_of,
            _flat_faces_with_holes,
            _reconstruct_curved_panels,
        )

        patches = list(_surface_patches(prims, angle_tol, ndigits))
        pcls = [(pt, classify_patch(prims, pt) if len(pt) >= min_patch_quads else "planar") for pt in patches]
        cyl_elids = {_elid_of(prims.names[j]) for pt, c in pcls if c == "cylinder" for j in pt}
        elid_prims: dict = {}
        for j, nm in enumerate(prims.names):
            elid_prims.setdefault(_elid_of(nm), []).append(j)
        out: list = []
        consumed: set = set()
        if blk is not None and blk.conn.shape[1] == 4:
            for _face, panel_elids in _reconstruct_curved_panels(blk, cyl_elids, ndigits, angle_tol, min_patch_quads):
                pj = [j for e in panel_elids for j in elid_prims.get(e, [])]
                if pj:
                    out.append((pj, "surface", "curved"))
                    consumed.update(panel_elids)
        for pt, c in pcls:
            if c == "cylinder":
                out.append((pt, "merged", "cylinder"))
                continue
            rem = [j for j in pt if _elid_of(prims.names[j]) not in consumed]
            if not rem:
                continue
            if len(rem) == 1:
                out.append((rem, "single", "facet"))
                continue
            if c == "planar" and _flat_faces_with_holes(prims, rem, ndigits):
                # production merges the whole flat patch to one face via robust extraction
                out.append((rem, "merged", "planar"))
                continue
            for idxs in _plane_buckets(prims, ndigits, rem):  # freeform / unresolved → coplanar/facet
                for comp in _component_labels(prims, idxs, ndigits):
                    out.append((comp, "single" if len(comp) == 1 else "coplanar", "facet"))
        return out

    if strategy == "coplanar":
        out: list = []
        for idxs in _plane_buckets(prims, ndigits):
            for comp in _component_labels(prims, idxs, ndigits):
                out.append((comp, "single" if len(comp) == 1 else "coplanar", ""))
        return out

    if strategy == "planar":
        md = _auto_max_dev(prims) if max_dev is None else max_dev
        out = []
        for p in _planar_patches(prims, md, ndigits):
            if len(p) == 1:
                out.append((p, "single", ""))
            elif _merge_succeeds(prims, p, ndigits):
                out.append((p, "merged", ""))  # whole flat patch collapses to one face
            else:  # boundary won't collapse -> coplanar fallback (mirrors the writer; never worse)
                for idxs in _plane_buckets(prims, ndigits, p):
                    for comp in _component_labels(prims, idxs, ndigits):
                        out.append((comp, "single" if len(comp) == 1 else "coplanar", ""))
        return out

    if strategy == "classify":
        # Recognize each smooth region-grown patch as an analytic primitive (planar /
        # cylinder / freeform). Achieved = one face per recognised patch; small leftovers
        # fall back to coplanar. This measures the analytic-emit potential (a jacket = a
        # handful of cylinders) before the STEP/IFC surface emit is wired.
        out = []
        leftovers = []
        for patch in _surface_patches(prims, angle_tol, ndigits):
            if len(patch) >= min_patch_quads:
                out.append((patch, "merged", classify_patch(prims, patch)))
            else:
                leftovers.extend(patch)
        if leftovers:
            for idxs in _plane_buckets(prims, ndigits, leftovers):
                for comp in _component_labels(prims, idxs, ndigits):
                    out.append((comp, "single" if len(comp) == 1 else "coplanar", "small"))
        return out

    # surface: big smooth patches become one fitted surface; the small leftover
    # patches fall back to the coplanar merge (mirrors reconstruct_shell_surfaces
    # with merge_fallback=True), so the count is a faithful surface+fallback preview.
    out = []
    leftovers = []
    for patch in _surface_patches(prims, angle_tol, ndigits):
        if len(patch) >= min_patch_quads:
            out.append((patch, "surface", ""))
        else:
            leftovers.extend(patch)
    if leftovers:
        for idxs in _plane_buckets(prims, ndigits, leftovers):
            for comp in _component_labels(prims, idxs, ndigits):
                out.append((comp, "single" if len(comp) == 1 else "coplanar", ""))
    return out


def analyze_part(part, strategy: str = "coplanar", ndigits: int = 6, **params) -> PartitionResult:
    """Compute the planned merge partition for every FEM shell mesh under ``part``
    (Part or Assembly), labelling each primitive with its intended component and
    its achieved (post-fallback) merge group. Builds NO geometry.

    ``strategy`` selects the algorithm (see :data:`ALGORITHMS`):

    * ``none``     — identity: every primitive is its own group (the raw baseline,
      shows the unmerged fragmentation the writers face).
    * ``coplanar`` — plane-bucket + edge-connected components, merged when the union
      collapses to one clean loop (the current production merge).
    * ``planar`` — flat region growing: grow a patch while it stays within ``max_dev``
      of one plane, emit one flat plate each (recovers large flat panels coplanar's
      exact bucketing misses; curved skin becomes piecewise-flat). Wired into the writer.
    * ``surface`` — normal-continuity region growing: smooth patches (curved or flat)
      grow into one fitted surface (B-spline). Preview-only until the native fit lands.
    * ``classify`` — like ``surface``, but each patch is recognised as an analytic
      primitive (planar / cylinder / freeform) via least-squares fits, so the preview
      can report e.g. "a jacket = 16 cylinders" before the analytic emit is wired.
    * ``auto`` — mirrors the PRODUCTION analytic emit exactly (cylinder recognition +
      cross-T curved B-spline panels + planar whole-patch merge + facet residual), so the
      colorized preview matches what STEP/IFC/``generate`` actually build. Pair with
      ``mode="class"`` to colour by output class (cylinder=green, curved=purple,
      planar=blue, facet=red).
    * ``panel`` — structured-quad region growing (not implemented yet).

    ``ndigits`` is the coplanarity rounding tolerance; ``params`` tune the curved
    strategies: ``angle_tol`` (deg, default 30) is the max fold angle region growing
    crosses, ``min_patch_quads`` (default 12) the smallest patch worth a surface fit.
    """
    strategy = (strategy or "coplanar").lower()
    if strategy not in ALGORITHMS:
        raise ValueError(f"unknown merge algorithm {strategy!r}; available: {list(ALGORITHMS)}")
    if strategy == "panel":
        raise NotImplementedError("merge algorithm 'panel' (structured-quad growing) is not implemented yet")
    angle_tol = float(params.get("angle_tol", 30.0))
    min_patch_quads = int(params.get("min_patch_quads", 12))
    max_dev = params.get("max_dev", None)
    if max_dev is not None:
        max_dev = float(max_dev)

    res = PartitionResult()
    next_comp = 0
    next_ach = 0
    n_groups = 0
    n_surface = 0
    parts = part.get_all_parts_in_assembly(include_self=True) if hasattr(part, "get_all_parts_in_assembly") else [part]
    for p in parts:
        fem = getattr(p, "fem", None)
        if fem is None or len(fem.elements) == 0:
            continue
        for blk in _shell_blocks(fem):
            prims = _block_primitives(blk)
            if len(prims) == 0:
                continue
            # warped-quad-split halves are named "sh{eid}_1"; both halves are tris.
            split = {j for j, nm in enumerate(prims.names) if nm.endswith("_1")}

            for comp, kind, cls in _strategy_groups(prims, strategy, ndigits, angle_tol, min_patch_quads, max_dev, blk):
                n_groups += 1
                if kind == "surface":
                    n_surface += 1
                cgid = next_comp
                next_comp += 1
                # a surface/merged patch is one fitted face; a coplanar group collapses only
                # if its union is a single clean loop; a single primitive is trivially one plate.
                ok = True if kind in ("single", "surface", "merged") else _merge_succeeds(prims, comp, ndigits)
                agid = None
                if ok:
                    agid = next_ach
                    next_ach += 1
                for j in comp:
                    res.outlines.append(prims.outline(j))
                    res.component.append(cgid)
                    res.patch_class.append(cls)
                    if ok:
                        res.achieved.append(agid)
                    else:
                        res.achieved.append(next_ach)  # each fell-back prim is its own plate
                        next_ach += 1
                    res.is_split_tri.append(j in split)

    n_prim = len(res.outlines)
    comp_sizes: dict = {}
    for c in res.component:
        comp_sizes[c] = comp_sizes.get(c, 0) + 1
    ach_sizes: dict = {}
    for a in res.achieved:
        ach_sizes[a] = ach_sizes.get(a, 0) + 1
    multi = [c for c, n in comp_sizes.items() if n > 1]
    # a multi-primitive component "failed" if it did NOT collapse (its prims each
    # became their own achieved group): detect via achieved-group sizes per component.
    comp_to_ach: dict = {}
    for c, a in zip(res.component, res.achieved):
        comp_to_ach.setdefault(c, set()).add(a)
    failed = [c for c in multi if len(comp_to_ach[c]) > 1]

    res.stats = {
        "strategy": strategy,
        "ndigits": ndigits,
        "angle_tol": angle_tol if strategy in ("surface", "classify", "auto") else None,
        "min_patch_quads": min_patch_quads if strategy in ("surface", "classify", "auto") else None,
        "primitives": n_prim,
        "split_tris": int(sum(res.is_split_tri)),
        "groups": n_groups,
        "surface_patches": n_surface,
        "components_intended": len(comp_sizes),
        "achieved_plates": len(ach_sizes),
        "multi_components": len(multi),
        "components_merged": len(multi) - len(failed),
        "components_fell_back": len(failed),
        "largest_component": max(comp_sizes.values()) if comp_sizes else 0,
        "largest_achieved": max(ach_sizes.values()) if ach_sizes else 0,
        "reduction_actual": round(n_prim / max(1, len(ach_sizes)), 2),  # emit speedup we actually get
        "reduction_ideal": round(n_prim / max(1, len(comp_sizes)), 2),  # if every component merged
        "plates_lost_to_fallback": len(ach_sizes) - len(comp_sizes),
    }
    if strategy in ("classify", "auto"):
        # patches + facet coverage per fitted analytic primitive (the recogniser's verdict).
        ach_class: dict = {}
        for a, cls in zip(res.achieved, res.patch_class):
            ach_class[a] = cls  # all prims of an achieved group share a class
        patches_by_class: dict = {}
        facets_by_class: dict = {}
        for a, cls in zip(res.achieved, res.patch_class):
            facets_by_class[cls] = facets_by_class.get(cls, 0) + 1
        for a, cls in ach_class.items():
            patches_by_class[cls] = patches_by_class.get(cls, 0) + 1
        res.stats["patches_by_class"] = patches_by_class
        res.stats["facet_pct_by_class"] = {k: round(100.0 * v / max(1, n_prim), 1) for k, v in facets_by_class.items()}
    return res


# ── colorized GLB ────────────────────────────────────────────────────────────


def _color_for(gid: int) -> np.ndarray:
    """Deterministic bright RGBA per group id (hash spread across the wheel)."""
    h = (gid * 2654435761) & 0xFFFFFFFF
    r = 60 + (h & 0xFF) % 196
    g = 60 + ((h >> 8) & 0xFF) % 196
    b = 60 + ((h >> 16) & 0xFF) % 196
    return np.array([r, g, b, 255], dtype=np.uint8)


# Fixed colors per fitted analytic class (the ``class`` mode of the classify strategy).
_CLASS_COLORS = {
    "planar": [70, 120, 220, 255],  # blue
    "cylinder": [70, 190, 90, 255],  # green
    "cone": [230, 150, 40, 255],  # orange
    "freeform": [210, 60, 60, 255],  # red
    "small": [130, 130, 130, 255],  # grey (below the surface-fit threshold)
    # auto (production emit) classes:
    "curved": [150, 90, 220, 255],  # purple — reconstructed B-spline panel
    "facet": [210, 60, 60, 255],  # red — un-merged residual (per-facet / coplanar leftover)
    "": [130, 130, 130, 255],
}


def write_preview_glb(res: PartitionResult, out_path: str, mode: str = "achieved") -> str:
    """Write a GLB coloring each triangulated primitive by its merge group.

    ``mode='achieved'`` (default): color by the group that actually collapses — a
    clean merge is one solid-color blob, a fell-back region is confetti (each
    primitive its own color), so fragmentation is visible at a glance.
    ``mode='component'``: color by the *intended* group (what should have merged).
    ``mode='status'``: green = merged (collapses to 1 plate), red = fell back.
    ``mode='class'``: color by fitted analytic primitive (planar=blue, cylinder=green,
    freeform=red, small=grey) — pair with the ``classify`` strategy.
    """
    import trimesh

    label = res.achieved if mode in ("achieved", "status") else res.component
    comp_to_ach: dict = {}
    for c, a in zip(res.component, res.achieved):
        comp_to_ach.setdefault(c, set()).add(a)

    verts: list = []
    faces: list = []
    vcolors: list = []  # per-vertex (primitives are unwelded, so vertex colors == face colors, and
    # this avoids trimesh's O(N) face->vertex sparse conversion, which OOMs/errors at 100k+ faces)
    for i, outline in enumerate(res.outlines):
        base = len(verts)
        pts = np.asarray(outline, dtype=np.float64)
        verts.extend(pts.tolist())
        faces.extend((base, base + t + 1, base + t + 2) for t in range(len(pts) - 2))
        if mode == "status":
            failed = len(comp_to_ach[res.component[i]]) > 1
            col = [210, 60, 60, 255] if failed else [70, 180, 90, 255]
        elif mode == "class":
            col = _CLASS_COLORS.get(res.patch_class[i] if i < len(res.patch_class) else "", _CLASS_COLORS[""])
        else:
            col = _color_for(int(label[i])).tolist()
        vcolors.extend([col] * len(pts))

    mesh = trimesh.Trimesh(
        vertices=np.array(verts, dtype=np.float64),
        faces=np.array(faces, dtype=np.int64),
        vertex_colors=np.array(vcolors, dtype=np.uint8),
        process=False,
    )
    trimesh.Scene(mesh).export(out_path, file_type="glb")
    return out_path
