from __future__ import annotations

from typing import Iterable

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import ObjectMesh


def merge_mesh_objects(list_of_objects: Iterable[ObjectMesh]) -> ObjectMesh:
    pm = ObjectMesh(
        create_guid(),
        np.array([], dtype=int),
        np.array([], dtype=float),
        np.array([], dtype=float),
    )

    for obj in list_of_objects:
        pm += obj

    return pm
