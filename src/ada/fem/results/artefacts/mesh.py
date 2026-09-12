"""Mesh GLB writer and the mesh edge / line-edge / element sidecar writers."""

from __future__ import annotations

import os
import pathlib
import struct

import numpy as np

from .formats import (
    EDGE_HEADER_BYTES,
    EDGE_MAGIC,
    EDGE_VERSION,
    ELEM_HEADER_BYTES,
    ELEM_MAGIC,
    ELEM_VERSION,
)
from .specs import MeshGeometry

# ---------------------------------------------------------------------------
# Mesh GLB writer
# ---------------------------------------------------------------------------


def _compute_topology(geom: MeshGeometry):
    """Compute edges/faces/element-ranges from the geometry.

    Wraps :func:`get_mesh_topology` so the bake walks the per-element
    ``ElemShape`` machinery exactly once per source instead of once
    per writer.
    """

    from ada.fem.results.common import MeshData
    from ada.visit.rendering.femviz import get_mesh_topology

    mesh_data = MeshData(points=geom.points, cells=geom.cell_blocks)
    return get_mesh_topology(mesh_data)


def write_mesh_glb(geom: MeshGeometry, out_path: os.PathLike, *, faces=None) -> None:
    """Write a geometry-only GLB (vertices + face indices, no per-step
    or per-vertex data baked in). The frontend renders edges from the
    face topology via a wireframe pass; the legacy GLB pipeline still
    emits explicit edge geometry, but the streaming path doesn't need
    to.

    ``faces`` may be supplied by callers that have already computed
    the topology; when omitted, the function recomputes from
    ``geom``. Standalone-test callers leave it unset; the bake passes
    the precomputed list to avoid re-walking the mesh."""

    import trimesh
    from trimesh.visual.material import PBRMaterial

    if faces is None:
        faces = _compute_topology(geom).faces

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scene = trimesh.Scene()
    if faces:
        face_arr = np.asarray(faces, dtype=np.uint32).reshape(-1, 3)
        face_mesh = trimesh.Trimesh(vertices=geom.points, faces=face_arr, process=False)
        face_mesh.visual.material = PBRMaterial(doubleSided=True)
        scene.add_geometry(face_mesh, node_name="mesh", geom_name="faces")
    else:
        # Line-only models (beam fixtures): no faces. We still emit a
        # GLB so the manifest's mesh.url resolves; trimesh refuses to
        # export an empty scene, so seed it with a degenerate point
        # cloud at the bbox centre. The frontend treats face-less GLBs
        # via the line-element render path.
        empty = trimesh.PointCloud(vertices=geom.points)
        scene.add_geometry(empty, node_name="mesh", geom_name="points")

    with open(out_path, "wb") as f:
        scene.export(file_obj=f, file_type="glb")


# ---------------------------------------------------------------------------
# Mesh-edge sidecar writer
# ---------------------------------------------------------------------------


def write_mesh_edges(geom: MeshGeometry, out_path: os.PathLike, *, edges=None) -> int:
    """Write the per-element edges as a deduped uint32 pair list.

    Edges come from each cell's :class:`ElemShape` directly — they
    reflect the *element* boundaries, not the artefact diagonals
    introduced by triangulating quad faces. The frontend renders
    these as a wireframe overlay so users see actual element
    topology, which matters for higher-order or quad-faced cells
    where the visual triangulation would draw misleading edges.

    Adjacent solid elements share edges; we sort each pair and
    np.unique-dedupe so a typical hex mesh ends up with roughly half
    the line count.

    ``edges`` may be passed by callers that have already computed the
    topology; otherwise the function recomputes.
    """

    if edges is None:
        edges = _compute_topology(geom).edges

    if edges:
        edge_pairs = np.asarray(edges, dtype=np.uint32).reshape(-1, 2)
        sorted_pairs = np.sort(edge_pairs, axis=1)
        unique = np.unique(sorted_pairs, axis=0)
        n_edges = int(unique.shape[0])
        payload = unique.astype(np.uint32).tobytes(order="C")
    else:
        n_edges = 0
        payload = b""

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, n_edges)
        f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        f.write(payload)
    return n_edges


def write_mesh_line_edges(geom: MeshGeometry, out_path: os.PathLike) -> int:
    """Write the edges belonging to LINE elements, in the same format as
    :func:`write_mesh_edges`.

    A beam's mesh line and a shell's are both "element boundary", but they are not
    the same thing to look at: the shell edges are a grid you read element size
    off, the beam edges are members. Drawn in one colour the beams disappear into
    the grid. The frontend gives them their own, dimmer one — which it can only do
    if it knows which edges they are, and the main sidecar cannot say: it sorts and
    dedupes every element's edges together, so any grouping by element type is
    gone by the time it is written.

    Emitted as a SEPARATE list rather than by reordering the main one, so an older
    viewer reading a newer bake still draws every edge exactly as before, and a
    newer viewer reading an older bake simply finds nothing here and does the same.
    The frontend removes these pairs from the main set before drawing it, so no
    edge is drawn twice.
    """
    # ``cell_blocks`` are meshio-shaped: a canonical type STRING and a connectivity
    # array, not the ElemShape objects the topology walker sees.
    line_types = {"line", "line3"}

    pairs: list[tuple[int, int]] = []
    for block in geom.cell_blocks:
        if str(getattr(block, "cell_type", "")) not in line_types:
            continue
        refs = np.asarray(block.data)
        if refs.ndim != 2 or refs.shape[1] < 2:
            continue
        # First and last node. A 3-node line element is one member with a
        # mid-side node, not two segments, and the wireframe should say so.
        for row in refs:
            pairs.append((int(row[0]), int(row[-1])))

    if pairs:
        arr = np.asarray(pairs, dtype=np.uint32).reshape(-1, 2)
        unique = np.unique(np.sort(arr, axis=1), axis=0)
        n_edges = int(unique.shape[0])
        payload = unique.astype(np.uint32).tobytes(order="C")
    else:
        n_edges = 0
        payload = b""

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, n_edges)
        f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        f.write(payload)
    return n_edges


# ---------------------------------------------------------------------------
# Mesh-element sidecar writer
# ---------------------------------------------------------------------------


def write_mesh_elements(
    geom: MeshGeometry,
    out_path: os.PathLike,
    *,
    element_ranges=None,
) -> int:
    """Write per-element ``(label, tri_start, tri_count)`` ranges into
    the AFEM sidecar.

    Frontend turns these into ``userdata.id_hierarchy`` and
    ``userdata.draw_ranges_<meshName>`` so the FEA mesh slots into
    the existing CustomBatchedMesh pick + highlight pipeline. Labels
    are uint32; an element id larger than ``2**32 - 1`` would be
    truncated, so the writer raises rather than silently aliasing.

    ``element_ranges`` may be passed by callers that have already
    computed the topology; otherwise the function recomputes.
    """

    if element_ranges is None:
        element_ranges = _compute_topology(geom).element_ranges

    n_elements = len(element_ranges)
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "wb") as f:
        prefix = ELEM_MAGIC + struct.pack("<II", ELEM_VERSION, n_elements)
        f.write(prefix + b"\x00" * (ELEM_HEADER_BYTES - len(prefix)))

        if n_elements:
            arr = np.empty((n_elements, 3), dtype=np.uint32)
            for i, er in enumerate(element_ranges):
                if er.label < 0 or er.label >= 2**32:
                    raise ValueError(
                        f"AFEM label {er.label} for element index {i} doesn't "
                        f"fit in uint32; widen the format or normalise labels."
                    )
                arr[i, 0] = er.label
                arr[i, 1] = er.tri_start
                arr[i, 2] = er.tri_count
            f.write(arr.tobytes(order="C"))

    return n_elements
