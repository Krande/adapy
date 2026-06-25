from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshStore, MeshType

if TYPE_CHECKING:
    from ada.visit.gltf.graph import GraphStore


def coalesce_groups_by_node(indices: np.ndarray, groups: list[GroupReference]):
    """Reorder a flat index buffer so every index-range sharing a ``node_ref`` is
    contiguous, returning ``(new_indices, new_groups)`` with exactly one
    :class:`GroupReference` per distinct node (first-seen order, for stable output).

    Positions are NOT touched — indices are absolute vertex references, so moving whole
    index-ranges only changes draw order, not the triangle set. This is what lets
    same-name sibling solids that were merged onto one graph node become a single
    contiguous draw-range (the viewer maps one node -> one ``[start, length]``).

    No-op (returns the inputs unchanged) when every group already has a unique node."""
    from collections import OrderedDict

    buckets: "OrderedDict[object, list[tuple[int, int]]]" = OrderedDict()
    for g in groups:
        buckets.setdefault(g.node_ref, []).append((g.start, g.length))
    if len(buckets) == len(groups):  # already one group per node — nothing to merge
        return indices, groups

    parts = []
    new_groups: list[GroupReference] = []
    cursor = 0
    for node_ref, ranges in buckets.items():
        total = 0
        for start, length in ranges:
            parts.append(indices[start : start + length])
            total += length
        new_groups.append(GroupReference(node_ref, cursor, total))
        cursor += total
    new_indices = np.concatenate(parts).astype(np.uint32, copy=False) if parts else indices
    return new_indices, new_groups


def concatenate_stores(stores: Iterable[MeshStore], graph_store: GraphStore = None) -> MergedMesh | None:
    """Concatenate multiple MeshStore objects into a single MergedMesh object."""
    # Converting to list to avoid multiple iterations (e.g. if stores is a generator)
    stores = list(stores)

    if not stores:
        return None

    if len(stores) == 1:
        store = stores[0]
        return MergedMesh(
            store.indices,
            store.position,
            store.normal,
            store.material,
            store.type,
            (
                [GroupReference(store.node_ref, 0, len(store.indices))]
                if store.type != MeshType.POINTS
                else [GroupReference(store.node_ref, 0, len(store.position))]
            ),
        )

    groups = []
    position_list = []
    indices_list = []
    normal_list = []
    has_normal = stores[0].normal is not None
    sum_positions = 0
    sum_indices = 0

    for i, s in enumerate(stores):
        groups.append(GroupReference(s.node_ref, sum_indices, len(s.indices)))
        position_list.append(s.position)
        indices_list.append(s.indices + sum_positions // 3)
        if has_normal:
            normal_list.append(s.normal)

        sum_positions += len(s.position)
        sum_indices += len(s.indices)

    position = np.concatenate(position_list, dtype=np.float32)
    indices = np.concatenate(indices_list, dtype=np.uint32)
    normal = np.concatenate(normal_list) if has_normal else None
    return MergedMesh(indices, position, normal, stores[0].material, stores[0].type, groups)
