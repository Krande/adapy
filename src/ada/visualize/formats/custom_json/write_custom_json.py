from __future__ import annotations

import io
import json
import os
import pathlib
from typing import TYPE_CHECKING, Union

from .config import ExportConfig

if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.base.physical_objects import BackendGeom
    from ada.concepts.connections import JointBase
    from ada.fem.results import Results


def to_custom_json(
    ada_obj: Union[Assembly, Part, Results, JointBase, BackendGeom],
    output_file_path,
    data_type=None,
    export_config=ExportConfig(),
    return_file_obj=False,
):
    from ada import Part
    from ada.concepts.connections import JointBase
    from ada.fem.results import Results

    from .write_joints_to_json import export_joint_to_json
    from .write_part_to_json import export_part_to_json
    from .write_results_to_json import export_results_to_json

    if issubclass(type(ada_obj), Part):
        output = export_part_to_json(ada_obj, export_config)
    elif issubclass(type(ada_obj), JointBase):
        output = export_joint_to_json(ada_obj, export_config)
    elif isinstance(ada_obj, Results):
        if data_type is None:
            raise ValueError('Please pass in a "data_type" value in order to export results mesh')
        output = export_results_to_json(ada_obj, data_type)
    else:
        raise NotImplementedError(f'Currently not supporting export of type "{type(ada_obj)}"')

    if return_file_obj:
        return io.StringIO(json.dumps(output))

    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output, f, indent=4)


def bump_version(name, url, version_file, refresh_ver_file=False):
    version_file = pathlib.Path(version_file)
    os.makedirs(version_file.parent, exist_ok=True)
    if version_file.exists() is False:
        data = dict()
    else:
        with open(version_file, "r") as f:
            data = json.load(f)
    if refresh_ver_file:
        data = dict()

    if name not in data.keys():
        data[name] = dict(url=url, version=0)
    else:
        obj = data[name]
        obj["url"] = url
        obj["version"] += 1

    with open(version_file, "w") as f:
        json.dump(data, f, indent=4)
