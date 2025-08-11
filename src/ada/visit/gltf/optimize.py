from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshStore, MeshType

if TYPE_CHECKING:
    from ada.visit.gltf.graph import GraphStore


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
