from __future__ import annotations

import os
import pathlib
import pickle
import shutil
import subprocess
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult, FieldData, Mesh

_script_dir = pathlib.Path(__file__).parent.resolve().absolute()

ABA_IO = _script_dir / "aba_io.py"


def convert_to_pckle(odb_path, pickle_path, use_aba_version=None):
    aba_ver = "abaqus" if use_aba_version is None else use_aba_version
    aba_exe_path = pathlib.Path(shutil.which(aba_ver))

    odb_path = pathlib.Path(odb_path).resolve().absolute()

    if os.path.isfile(pickle_path):
        os.remove(pickle_path)

    logger.info(f'Extracting ODB data from "{odb_path.name}" using Abaqus/Python')

    backup_odb = odb_path.parent / f"{odb_path.stem}_backup.odb"
    if backup_odb.exists() is False:
        logger.info(f'Copying a backup of the odb file to "{backup_odb}" in case python corrupts the odb file')
        shutil.copy(odb_path, backup_odb)

    res = subprocess.run([aba_exe_path, "python", ABA_IO, odb_path], cwd=ABA_IO.parent, capture_output=True)
    logger.info(str(res.stdout, encoding="utf-8"))
    stderr = str(res.stderr, encoding="utf-8")
    if stderr != "":
        logger.error(stderr)


def get_odb_data(odb_path, overwrite=False, use_aba_version=None):
    odb_path = pathlib.Path(odb_path)
    pickle_path = odb_path.with_suffix(".pckle")

    if pickle_path.exists() is False or overwrite is True:
        convert_to_pckle(odb_path, pickle_path, use_aba_version)

    with open(pickle_path, "rb") as f:
        data = pickle.load(f)

    return data


def read_odb_pckle_file(result_file_path: str | pathlib.Path, overwrite=False) -> FEAResult:
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import FEAResult

    if isinstance(result_file_path, pathlib.Path) is False:
        result_file_path = pathlib.Path(result_file_path)

    if result_file_path.suffix.lower() == ".odb":
        result_file_path = result_file_path.with_suffix(".pckle")

    if result_file_path.exists() is False or overwrite is True:
        convert_to_pckle(result_file_path.with_suffix(".odb"), result_file_path)

    with open(result_file_path, "rb") as f:
        data = pickle.load(f)

    mesh = get_odb_instance_data(data["rootAssembly"]["instances"])
    fields = get_odb_frame_data(data["steps"])

    return FEAResult(
        name=result_file_path.stem,
        software=FEATypes.ABAQUS,
        mesh=mesh,
        results=fields,
        results_file_path=result_file_path,
    )


def get_odb_field_data(field_name, field_data, frame_num):
    from ada.fem.results.field_data import ElementFieldData, NodalFieldData

    field_type, components, data = field_data

    if field_type == "ELEMENT_NODAL":
        field_values = np.array(list(yield_elem_nodal_data(data)))
        return ElementFieldData(field_name, frame_num, components, values=field_values)
    elif field_type == "NODAL":
        field_values = np.array(list(yield_nodal_data(data)))
        return NodalFieldData(field_name, frame_num, components, field_values)
    else:
        raise NotImplementedError()


def get_odb_frame_data(steps: dict) -> list[FieldData]:
    frame_num = 0
    fields = []
    for step_name, step_data in dict(sorted(steps.items(), key=lambda x: x[1]["totalTime"])).items():
        for frame in step_data["frames"]:
            for key, value in frame.items():
                field = get_odb_field_data(key, value["values"], frame_num)
                fields.append(field)
            frame_num += 1

    return fields


def get_odb_instance_data(instances) -> Mesh:
    from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
    from ada.fem.formats.general import FEATypes
    from ada.fem.results.common import ElementBlock, ElementInfo, FemNodes, Mesh

    if len(instances) > 1:
        raise NotImplementedError("Multi-instances results are not yet supported")

    instance = instances[0]

    ids, coords = zip(*instance["nodes"])
    el_ids, el_type_array, nodes_connectivity, sec_cat = zip(*instance["elements"])
    el_type_set = set(el_type_array)
    if len(el_type_set) != 1:
        raise NotImplementedError("Mixed element sets not yet supported")

    el_type = el_type_array[0]
    shape = abaqus_el_type_to_ada(el_type)
    elem_info = ElementInfo(type=shape, source_software=FEATypes.ABAQUS, source_type=el_type)
    el_block = ElementBlock(
        elem_info=elem_info, node_refs=np.array(nodes_connectivity, dtype=int), identifiers=np.array(el_ids, dtype=int)
    )
    el_blocks = [el_block]
    nodes = FemNodes(coords=np.array(coords, dtype=float), identifiers=np.array(ids, dtype=int))

    return Mesh(elements=el_blocks, nodes=nodes)


def yield_elem_nodal_data(data):
    for x in data:
        spn = x["sec_p_num"]
        if isinstance(spn, dict):
            spn = -1
        if isinstance(x["data"], list) is False:
            x["data"] = [x["data"]]

        yield x["elementLabel"], spn, x["nodeLabel"], *x["data"]


def yield_nodal_data(data):
    for x in data:
        yield x[0], *x[1]
