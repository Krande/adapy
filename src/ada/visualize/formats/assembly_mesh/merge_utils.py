from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Tuple, Union

import numpy as np

from ada.ifc.utils import create_guid
from ada.visualize.concept import ObjectMesh
from ada.visualize.formats.assembly_mesh.write_objects_to_mesh import obj_to_mesh

if TYPE_CHECKING:
    from ada import Beam, Plate


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


def merge_objects_into_single_json(
    guid,
    list_of_objects: Iterable[Union[Beam, Plate]],
    export_config,
    obj_num,
    all_num,
) -> Tuple[ObjectMesh, int]:

    pm = ObjectMesh(
        guid,
        np.array([], dtype=int),
        np.array([], dtype=float),
        np.array([], dtype=float),
    )

    for obj in list_of_objects:
        print(f"Converting {obj_num} of {all_num} to PolyModel")
        res = obj_to_mesh(obj, export_config=export_config)
        if res is None:
            continue
        pm += res
        obj_num += 1

    return pm, obj_num
