"""Feature edges for offscreen images: the edge lines Ada Studio draws over a CAD model.

Same rule as the viewer's edge overlay (frontend ``utils/mesh_select/edgeCore.ts``, with "hide
tessellation edges" on, its default): vertices welded by position (a tessellated GLB is a triangle
soup), then every boundary edge plus every edge whose two faces meet at more than ``angle_deg``.
The lines carry no colour, so the adacpp renderer draws them as edges (1 px, its ``line_color``),
as it does an FEA mesh's element edges; coloured lines would draw as thick beam members.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

#: The viewer's feature-edge threshold (degrees), with tessellation edges hidden.
FEATURE_EDGE_ANGLE_DEG = 30.0
# Weld tolerance, as the viewer's: positions quantised to 1e-4 model units.
_WELD_DECIMALS = 4


def feature_edge_pairs(
    vertices: np.ndarray, faces: np.ndarray, angle_deg: float = FEATURE_EDGE_ANGLE_DEG
) -> tuple[np.ndarray, np.ndarray]:
    """``(welded vertices, (n, 2) vertex pairs)`` of a triangle mesh's feature edges."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if len(faces) == 0:
        return vertices, np.zeros((0, 2), dtype=np.int64)
    _, first, inverse = np.unique(np.round(vertices, _WELD_DECIMALS), axis=0, return_index=True, return_inverse=True)
    welded = vertices[first]
    f = inverse.reshape(-1)[faces]
    # drop triangles a weld collapsed
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    if len(f) == 0:
        return welded, np.zeros((0, 2), dtype=np.int64)
    normals = np.cross(welded[f[:, 1]] - welded[f[:, 0]], welded[f[:, 2]] - welded[f[:, 0]])
    length = np.linalg.norm(normals, axis=1)
    normals = normals / np.where(length > 0, length, 1.0)[:, None]

    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    owner = np.tile(np.arange(len(f)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same_as_next = np.all(edges[1:] == edges[:-1], axis=1)
    starts = np.flatnonzero(np.concatenate([[True], ~same_as_next]))
    counts = np.diff(np.concatenate([starts, [len(edges)]]))

    keep = counts != 2  # boundary (1) and non-manifold (>2) edges always show
    pair = counts == 2
    a, b = owner[starts[pair]], owner[starts[pair] + 1]
    dots = np.einsum("ij,ij->i", normals[a], normals[b])
    keep[pair] = dots <= np.cos(np.radians(angle_deg))
    return welded, edges[starts[keep]]


def glb_with_feature_edges(src: str | Path, dest: str | Path, angle_deg: float = FEATURE_EDGE_ANGLE_DEG) -> Path:
    """Write ``src`` to ``dest`` with its feature edges added as (uncoloured) line primitives."""
    import trimesh
    from trimesh.path.entities import Line
    from trimesh.path.path import Path3D

    scene = trimesh.load(str(src), force="scene")
    lines_v, lines_e, offset = [], [], 0
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geom = scene.geometry.get(geom_name)
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        world = trimesh.transformations.transform_points(np.asarray(geom.vertices), transform)
        verts, pairs = feature_edge_pairs(world, geom.faces, angle_deg)
        if len(pairs):
            lines_v.append(verts)
            lines_e.append(pairs + offset)
            offset += len(verts)
    if lines_e:
        pairs = np.concatenate(lines_e)
        scene.add_geometry(
            Path3D(
                entities=[Line(points=p.tolist()) for p in pairs],
                vertices=np.concatenate(lines_v).astype(np.float32),
            ),
            geom_name="feature_edges",
        )
    dest = Path(dest)
    scene.export(file_obj=str(dest), file_type="glb")
    return dest
