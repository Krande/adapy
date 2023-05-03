from typing import Iterable

import numpy as np

from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshStore


def concatenate_stores(stores: Iterable[MeshStore]) -> MergedMesh | None:
    """Concatenate multiple MeshStore objects into a single MergedMesh object."""
    stores = list(stores)
    if not stores:
        return None

    groups = []
    position_list = []
    indices_list = []
    normal_list = []
    has_normal = stores[0].normal is not None
    sum_positions = 0
    sum_indices = 0

    for i, s in enumerate(stores):
        groups.append(GroupReference(s.node_id, sum_indices, len(s.indices)))
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
