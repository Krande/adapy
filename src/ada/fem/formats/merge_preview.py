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
    _block_primitives,
    _canonical_sign,
    _shell_blocks,
)


@dataclass
class PartitionResult:
    """The planned partition of one assembly's shell primitives + its stats."""

    outlines: list = field(default_factory=list)  # (k,3) float arrays, per primitive
    component: list = field(default_factory=list)  # intended merge-group id per primitive
    achieved: list = field(default_factory=list)  # actually-collapsed group id per primitive
    is_split_tri: list = field(default_factory=list)  # primitive came from a warped-quad split (curved hint)
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
ALGORITHMS = ("none", "coplanar", "surface", "panel")


def _plane_buckets(prims, ndigits):
    """(material, thickness, canonical-normal, offset) buckets — mirrors
    ``_coplanar_block`` / ``_plate_plane_key``. Returns list[list[prim_idx]]."""
    tol = 10.0 ** (-ndigits)
    normals = prims.normals
    sign = _canonical_sign(normals, tol)
    ncanon = np.round(normals * sign[:, None], ndigits)
    p0 = prims.coords[np.array([r[0] for r in prims.rows], dtype=np.int64)]
    offset = np.round(sign * np.sum(normals * p0, axis=1), ndigits)
    thick_q = np.round(np.array(prims.ts), ndigits)
    buckets: dict = {}
    for j in range(len(prims)):
        key = (prims.mats[j], float(thick_q[j]), tuple(ncanon[j]), float(offset[j]))
        buckets.setdefault(key, []).append(j)
    return list(buckets.values())


def analyze_part(part, strategy: str = "coplanar", ndigits: int = 6, **params) -> PartitionResult:
    """Compute the planned merge partition for every FEM shell mesh under ``part``
    (Part or Assembly), labelling each primitive with its intended component and
    its achieved (post-fallback) merge group. Builds NO geometry.

    ``strategy`` selects the algorithm (see :data:`ALGORITHMS`):

    * ``none``     — identity: every primitive is its own group (the raw baseline,
      shows the unmerged fragmentation the writers face).
    * ``coplanar`` — plane-bucket + edge-connected components, merged when the union
      collapses to one clean loop (the current production merge).
    * ``surface`` / ``panel`` — curved-region growing (not yet implemented; wired
      here so the utility exposes them once the Phase-2 algorithm lands).

    ``ndigits`` is the coplanarity rounding tolerance; extra ``params`` (e.g.
    ``angle_tol``, ``min_patch_quads``) are forwarded to the curved strategies.
    """
    strategy = (strategy or "coplanar").lower()
    if strategy not in ALGORITHMS:
        raise ValueError(f"unknown merge algorithm {strategy!r}; available: {list(ALGORITHMS)}")
    if strategy in ("surface", "panel"):
        raise NotImplementedError(
            f"merge algorithm {strategy!r} (curved-region growing) is not implemented yet — "
            "coplanar/none only for now"
        )

    res = PartitionResult()
    next_comp = 0
    next_ach = 0
    n_buckets = 0
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

            if strategy == "none":
                buckets = [[j] for j in range(len(prims))]  # every primitive its own bucket
            else:  # coplanar
                buckets = _plane_buckets(prims, ndigits)
            n_buckets += len(buckets)

            for idxs in buckets:
                comps = [[j] for j in idxs] if strategy == "none" else _component_labels(prims, idxs, ndigits)
                for comp in comps:
                    cgid = next_comp
                    next_comp += 1
                    ok = True if len(comp) == 1 else _merge_succeeds(prims, comp, ndigits)
                    agid = None
                    if ok:
                        agid = next_ach
                        next_ach += 1
                    for j in comp:
                        res.outlines.append(prims.outline(j))
                        res.component.append(cgid)
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
        "primitives": n_prim,
        "split_tris": int(sum(res.is_split_tri)),
        "plane_buckets": n_buckets,
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
    return res


# ── colorized GLB ────────────────────────────────────────────────────────────


def _color_for(gid: int) -> np.ndarray:
    """Deterministic bright RGBA per group id (hash spread across the wheel)."""
    h = (gid * 2654435761) & 0xFFFFFFFF
    r = 60 + (h & 0xFF) % 196
    g = 60 + ((h >> 8) & 0xFF) % 196
    b = 60 + ((h >> 16) & 0xFF) % 196
    return np.array([r, g, b, 255], dtype=np.uint8)


def write_preview_glb(res: PartitionResult, out_path: str, mode: str = "achieved") -> str:
    """Write a GLB coloring each triangulated primitive by its merge group.

    ``mode='achieved'`` (default): color by the group that actually collapses — a
    clean merge is one solid-color blob, a fell-back region is confetti (each
    primitive its own color), so fragmentation is visible at a glance.
    ``mode='component'``: color by the *intended* group (what should have merged).
    ``mode='status'``: green = merged (collapses to 1 plate), red = fell back.
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
