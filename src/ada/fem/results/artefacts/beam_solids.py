"""Beam-solid mesh tessellation and its GLB / warp / element / edge sidecar writers."""

from __future__ import annotations

import os
import pathlib
import struct
from collections import defaultdict

import numpy as np

from .formats import (
    BEAM_WARP_HEADER_BYTES,
    BEAM_WARP_MAGIC,
    BEAM_WARP_VERSION,
    EDGE_HEADER_BYTES,
    EDGE_MAGIC,
    EDGE_VERSION,
)
from .mesh import write_mesh_elements
from .specs import MeshGeometry, SolidBeamMesh


def _dedup_beam_tessellation(
    verts: np.ndarray,
    tris: np.ndarray,
    t_vals: np.ndarray,
    *,
    position_tolerance: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse coincident-position vertices within a single beam's
    OCC tessellation.

    OCC tessellates each BRep face independently — side panels and
    end caps emit duplicate vertices at face boundaries. All such
    duplicates are at the same 3D position, the same axial position
    (so the same ``t``) and the same line element (so the same
    ``(n0, n1)``), which makes position-based merging lossless. The
    caller is responsible for NOT calling this across beam
    boundaries; that would corrupt AFBV at joints.

    Returns ``(unique_verts, remapped_tris, unique_t)`` where:

    * ``unique_verts`` is a deduplicated (n_unique, 3) array — the
      position from any merged buddy works since they're coincident.
    * ``remapped_tris`` has the same shape as the input ``tris`` but
      every vertex index now points into ``unique_verts``.
    * ``unique_t`` is the axial parameter for each merged vertex.
    """
    if verts.shape[0] == 0:
        return verts, tris.astype(np.uint32, copy=False), t_vals

    # Round to a fixed grid so floating-point jitter from the CAD kernel
    # doesn't cause coincident vertices to land in different buckets.
    rounded = np.round(verts / position_tolerance).astype(np.int64)
    _, inverse = np.unique(rounded, axis=0, return_inverse=True)
    n_unique = int(inverse.max()) + 1

    # Pick the first-encountered position for each bucket (or last
    # — they're coincident so it doesn't matter). Same for t.
    unique_verts = np.empty((n_unique, 3), dtype=verts.dtype)
    unique_verts[inverse] = verts

    unique_t = np.empty(n_unique, dtype=t_vals.dtype)
    unique_t[inverse] = t_vals

    remapped = inverse[tris].astype(np.uint32, copy=False)
    return unique_verts, remapped, unique_t


def tessellate_beams_to_solid_mesh(
    beams,
    *,
    extra_skip_reasons: dict | None = None,
    total_beams: int | None = None,
) -> "SolidBeamMesh | None":
    """Run OCC tessellation over a list of beams and produce a SolidBeamMesh.

    Each ``beams`` entry is a ``(beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos)``
    tuple where ``beam`` is a fully-constructed :class:`ada.Beam` (its
    ``section``, ``material``, ``up``, and endpoints supply everything OCC
    needs), ``elem_id`` is the source line-element id used as the per-beam
    label in the AFEL element-range table, ``n0_idx`` / ``n1_idx`` are the
    0-based positions of the parent line-element's endpoint nodes in the
    bake's main point buffer (used by the AFBV warp sidecar to lerp
    displacement onto the solid surface), and ``n0_pos`` / ``n1_pos`` are
    the world-space coordinates of those endpoints (used to compute the
    axial parameter ``t`` per vertex).

    Returns ``None`` when zero beams successfully tessellated — the bake
    then omits the beam-solids artefacts from the manifest.

    Per-beam failures are bucketed by reason into ``skip_reasons`` and
    logged once as a summary; the offending beam is dropped from the
    output rather than aborting the whole bake.

    Callers that pre-filter beams (e.g. ``no-section`` or
    ``genbeam-no-profile`` cases the reader can detect cheaply before
    OCC sees them) can pass those counts in via ``extra_skip_reasons``
    so the coverage summary reflects the full picture; ``total_beams``
    overrides the auto-default of ``len(beams)`` for the same reason.
    """

    from ada.config import get_logger
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.rendering.femviz import ElementRange

    bt = BatchTessellator()

    all_positions: list[np.ndarray] = []
    all_indices: list[np.ndarray] = []
    all_n0: list[np.ndarray] = []
    all_n1: list[np.ndarray] = []
    all_t: list[np.ndarray] = []
    ranges: list[ElementRange] = []
    vertex_offset = 0
    tri_cursor = 0
    skip_reasons: dict[str, int] = defaultdict(int)
    if extra_skip_reasons:
        for k, v in extra_skip_reasons.items():
            skip_reasons[k] += int(v)
    if total_beams is None:
        total_beams = len(beams) + sum(skip_reasons.values())
    success_count = 0

    for beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos in beams:
        try:
            geom = beam.solid_geom()
            ms = bt.tessellate_geom(geom, beam)
        except Exception as e:  # noqa: BLE001 — defensive
            skip_reasons[f"occ-error[{type(e).__name__}]"] += 1
            get_logger().debug(
                "beam-solid OCC failure elem %s: %s",
                elem_id,
                e,
            )
            continue

        pos = getattr(ms, "position", None)
        idx = getattr(ms, "indices", None)
        if pos is None or idx is None or pos.size == 0 or idx.size == 0:
            skip_reasons["empty-tessellation"] += 1
            continue

        verts_raw = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
        tris_local = np.asarray(idx, dtype=np.uint32).reshape(-1, 3)

        p0 = np.asarray(n0_pos, dtype=np.float64)
        p1 = np.asarray(n1_pos, dtype=np.float64)
        axis = p1 - p0
        axis_sq = float(np.dot(axis, axis))
        if axis_sq <= 0:
            # Zero-length beam: every vertex t=0 so disp collapses to disp[n0].
            t_vals_raw = np.zeros(verts_raw.shape[0], dtype=np.float32)
        else:
            rel = verts_raw - p0
            t_vals_raw = np.clip(rel @ axis / axis_sq, 0.0, 1.0).astype(np.float32)

        verts, tris_local_dedup, t_vals = _dedup_beam_tessellation(
            verts_raw,
            tris_local,
            t_vals_raw,
        )
        tris = tris_local_dedup + vertex_offset

        all_positions.append(verts)
        all_indices.append(tris.astype(np.uint32, copy=False))
        all_n0.append(np.full(verts.shape[0], n0_idx, dtype=np.uint32))
        all_n1.append(np.full(verts.shape[0], n1_idx, dtype=np.uint32))
        all_t.append(t_vals)

        tri_count = int(tris.shape[0])
        ranges.append(
            ElementRange(
                label=int(elem_id),
                tri_start=tri_cursor,
                tri_count=tri_count,
            )
        )
        vertex_offset += int(verts.shape[0])
        tri_cursor += tri_count
        success_count += 1

    if total_beams:
        skip_summary = ", ".join(f"{k}={v}" for k, v in sorted(skip_reasons.items())) or "none"
        get_logger().info(
            "beam-solid coverage: %d of %d beams tessellated (skip: %s)",
            success_count,
            total_beams,
            skip_summary,
        )

    if not all_positions:
        return None

    return SolidBeamMesh(
        points=np.concatenate(all_positions, axis=0),
        triangles=np.concatenate(all_indices, axis=0),
        element_ranges=ranges,
        vertex_node0=np.concatenate(all_n0, axis=0),
        vertex_node1=np.concatenate(all_n1, axis=0),
        vertex_t=np.concatenate(all_t, axis=0),
        total_beams=total_beams,
        skip_reasons=dict(skip_reasons),
    )


# ---------------------------------------------------------------------------
# Beam-solid mesh + sidecar writers
# ---------------------------------------------------------------------------


def write_beam_solids_glb(mesh: SolidBeamMesh, out_path: os.PathLike) -> None:
    """Write the concatenated beam-solid mesh as a geometry-only GLB.

    Same shape as :func:`write_mesh_glb` but takes an explicit
    ``(points, triangles)`` rather than going through
    :func:`_compute_topology`. ``trimesh.Trimesh(process=False)`` —
    skipping process is critical, otherwise trimesh merges duplicate
    vertices and the per-beam draw ranges go stale.
    """

    import trimesh
    from trimesh.visual.material import PBRMaterial

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    face_arr = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1, 3)
    tm = trimesh.Trimesh(vertices=mesh.points, faces=face_arr, process=False)
    tm.visual.material = PBRMaterial(doubleSided=True)
    scene = trimesh.Scene()
    scene.add_geometry(tm, node_name="beam_solids", geom_name="faces")
    with open(out_path, "wb") as f:
        scene.export(file_obj=f, file_type="glb")


def write_beam_solids_warp(mesh: SolidBeamMesh, out_path: os.PathLike) -> int:
    """Write the AFBV sidecar: per-vertex (node0_idx, node1_idx, t).

    Frontend reads these once at load and computes the beam-solid
    mesh's morph delta on every apply as
    ``lerp(disp[node0], disp[node1], t)`` per vertex. Result is the
    parent beam's two endpoints driving every vertex along the beam,
    so a scaled deformation keeps the solid mesh connected to the
    rest of the structure at the endpoints.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n0 = np.asarray(mesh.vertex_node0, dtype=np.uint32)
    n1 = np.asarray(mesh.vertex_node1, dtype=np.uint32)
    t = np.asarray(mesh.vertex_t, dtype=np.float32)
    n_verts = int(n0.shape[0])
    if not (n0.shape == n1.shape == t.shape):
        raise ValueError(f"AFBV shape mismatch: n0={n0.shape}, n1={n1.shape}, t={t.shape}")

    with open(out_path, "wb") as f:
        prefix = BEAM_WARP_MAGIC + struct.pack("<II", BEAM_WARP_VERSION, n_verts)
        f.write(prefix + b"\x00" * (BEAM_WARP_HEADER_BYTES - len(prefix)))
        if n_verts:
            # Interleaved layout: one (n0, n1, t) record per vertex —
            # frontend reads three typed arrays from one fetch by
            # striding into the same ArrayBuffer at the right offsets.
            payload = np.empty(n_verts * 3, dtype=np.uint32)
            payload[0::3] = n0
            payload[1::3] = n1
            # ``t`` is float32 but the underlying bits land in the
            # uint32 slot — view-cast keeps the float bit pattern.
            payload[2::3] = t.view(np.uint32)
            f.write(payload.tobytes(order="C"))
    return n_verts


def write_beam_solids_elements(mesh: SolidBeamMesh, out_path: os.PathLike) -> int:
    """Write the per-beam ``(label, tri_start, tri_count)`` sidecar in
    the AFEM format. Same magic + version as the main-mesh elements
    sidecar so the frontend's existing :func:`parseMeshElements`
    parser reads it without modification.
    """

    return write_mesh_elements(
        # Geometry-only wrapper just so we satisfy the existing
        # write_mesh_elements signature; element_ranges is the
        # actually-used field. The geom argument is ignored when
        # element_ranges is passed in directly.
        MeshGeometry(points=mesh.points, cell_blocks=[]),
        out_path,
        element_ranges=mesh.element_ranges,
    )


def write_beam_solids_edges(
    mesh: SolidBeamMesh,
    out_path: os.PathLike,
    *,
    position_tolerance: float = 1e-6,
) -> int:
    """Write the beam-solid element-boundary edges as AFEG.

    The triangulated beam-solid mesh has no inherent line topology —
    each beam is an extruded cross-section, every triangle's three
    edges look identical to the wireframe pass. Without separating
    "internal triangulation diagonal" from "this is where one beam
    element ends and the next begins" we'd either draw all 3N edges
    (visual mush) or none (the current state — beams look like one
    continuous tube).

    The element ranges from the AFEM sidecar already tell us which
    triangle belongs to which line-element label. So an edge is a
    boundary edge if either:

    * Only one triangle uses it (true mesh perimeter — open beam
      ends or genuinely free edges).
    * Two-or-more triangles use it but they live in different
      elements (the seam between adjacent beam-elements along the
      axis).

    Interior edges (multiple triangles, all in the same element) are
    dropped — those are the triangulation artefacts of the extrusion.

    **Edges are keyed by 3D position, not vertex index.** OCC
    tessellates each FACE of a solid independently and each beam
    independently, so the side panel and end cap of a single beam
    have different vertex indices at the same 3D positions. An
    index-based comparison would treat their shared edge as a
    one-triangle boundary edge on each side and draw the cross-
    hatching artefacts the user sees. Position-bucketing collapses
    coincident vertices so within-beam face seams correctly resolve
    as same-element interior edges and get dropped. Beam-to-beam
    joints (where two elements share node positions) still survive
    because their bucket-edges have triangles from different
    elements.

    Output is the AFEG format already used by the main mesh wireframe,
    so the frontend wires up the same :class:`THREE.LineSegments`
    sharing the beam-solid's position + morph attributes.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tris = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1, 3)
    n_tris = int(tris.shape[0])

    if n_tris == 0 or not mesh.element_ranges:
        with open(out_path, "wb") as f:
            prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, 0)
            f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        return 0

    # Per-triangle element label. Triangles outside any explicit range
    # are left at a sentinel so an edge that bridges "labeled" and
    # "unlabeled" still counts as a boundary — defensive against a
    # reader that ships partial coverage.
    tri_label = np.full(n_tris, np.iinfo(np.int64).max, dtype=np.int64)
    for er in mesh.element_ranges:
        if er.tri_count <= 0:
            continue
        s = int(er.tri_start)
        e = s + int(er.tri_count)
        tri_label[s:e] = int(er.label)

    # Bucket vertices by rounded 3D position so coincident vertices
    # from independent OCC face tessellations (within one beam) or
    # from adjacent beams at a shared joint resolve to the same
    # bucket id. ``np.unique(axis=0, return_inverse=True)`` returns a
    # deterministic mapping from row → group id sized to the number
    # of unique rows; we use the inverse as our bucket assignment.
    points = np.asarray(mesh.points, dtype=np.float64).reshape(-1, 3)
    rounded = np.round(points / position_tolerance).astype(np.int64)
    _, bucket_id = np.unique(rounded, axis=0, return_inverse=True)
    bucket_id = bucket_id.astype(np.uint64)

    # Three edges per triangle. Original vertex indices kept for the
    # final write so the frontend's LineSegments indexes into the
    # beam-solid GLB's existing position attribute. Bucket pairs are
    # used only for the grouping/dedup pass.
    e01 = np.stack([tris[:, 0], tris[:, 1]], axis=1)
    e12 = np.stack([tris[:, 1], tris[:, 2]], axis=1)
    e20 = np.stack([tris[:, 2], tris[:, 0]], axis=1)
    edges = np.concatenate([e01, e12, e20], axis=0)
    edges_sorted = np.sort(edges, axis=1)
    edge_labels = np.tile(tri_label, 3)

    # Bucket-based edge key: (min_bucket, max_bucket) packed into a
    # single uint64. Bucket ids fit in 32 bits unless the mesh has
    # ≥ 2^32 unique vertex positions (it doesn't).
    bucket_edges = np.empty_like(edges, dtype=np.uint64)
    bucket_edges[:, 0] = bucket_id[edges[:, 0]]
    bucket_edges[:, 1] = bucket_id[edges[:, 1]]
    bucket_edges_sorted = np.sort(bucket_edges, axis=1)
    key = (bucket_edges_sorted[:, 0] << np.uint64(32)) | bucket_edges_sorted[:, 1]
    order = np.argsort(key, kind="stable")
    key_sorted = key[order]
    labels_sorted = edge_labels[order]
    edges_sorted_by_key = edges_sorted[order]

    # Group boundaries: a unique edge spans key_sorted[start:next_start].
    is_new_group = np.empty(key_sorted.shape[0], dtype=bool)
    is_new_group[0] = True
    is_new_group[1:] = key_sorted[1:] != key_sorted[:-1]
    group_starts = np.flatnonzero(is_new_group)
    # Append n so np.diff gives the size of the final group.
    group_starts_ext = np.concatenate([group_starts, np.array([key_sorted.shape[0]], dtype=group_starts.dtype)])
    group_sizes = np.diff(group_starts_ext)

    # Vectorized "does any label in this group differ from the first?":
    # repeat the first label across each group, compare to the per-row
    # label, then reduce by sum per group.
    first_label_per_row = np.repeat(labels_sorted[group_starts], group_sizes)
    differs = labels_sorted != first_label_per_row
    diff_per_group = np.add.reduceat(differs.astype(np.int64), group_starts)

    # Keep edges that are either mesh-boundary (one triangle) or span
    # two elements (some label in the group differs from the first).
    keep_mask = (group_sizes == 1) | (diff_per_group > 0)
    if not np.any(keep_mask):
        kept_pairs = np.empty((0, 2), dtype=np.uint32)
    else:
        kept_pairs = edges_sorted_by_key[group_starts[keep_mask]].astype(np.uint32)

    n_edges = int(kept_pairs.shape[0])
    payload = kept_pairs.tobytes(order="C") if n_edges else b""

    with open(out_path, "wb") as f:
        prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, n_edges)
        f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        f.write(payload)
    return n_edges
