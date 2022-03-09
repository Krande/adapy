from __future__ import annotations

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
    from ada.visualize.concept import AssemblyMesh


def to_assembly_mesh(
    ada_obj: Union[Assembly, Part, Results, JointBase, BackendGeom],
    output_file_path=None,
    data_type=None,
    export_config: ExportConfig = ExportConfig(),
    return_file_obj=False,
    indent=None,
) -> Union[None, AssemblyMesh]:
    from ada import Part
    from ada.concepts.connections import JointBase
    from ada.fem.results import Results

    from .write_joints_to_mesh import export_joint_to_assembly_mesh
    from .write_part_to_mesh import export_part_to_assembly_mesh
    from .write_results_to_mesh import export_results_to_assembly_mesh

    if issubclass(type(ada_obj), Part):
        assembly_mesh = export_part_to_assembly_mesh(ada_obj, export_config)
    elif issubclass(type(ada_obj), JointBase):
        assembly_mesh = export_joint_to_assembly_mesh(ada_obj, export_config)
    elif isinstance(ada_obj, Results):
        if data_type is None:
            raise ValueError('Please pass in a "data_type" value in order to export results mesh')
        assembly_mesh = export_results_to_assembly_mesh(ada_obj, data_type)
    else:
        raise NotImplementedError(f'Currently not supporting export of type "{type(ada_obj)}"')

    if assembly_mesh is None:
        print("No writable elements found")
        return None

    if return_file_obj or output_file_path is None:
        return assembly_mesh

    output_assembly_mesh = assembly_mesh
    if export_config.merge_by_colour is True and isinstance(ada_obj, Results) is False:
        output_assembly_mesh = assembly_mesh.merge_objects_in_parts_by_color()

    output_file_path = pathlib.Path(output_file_path)
    os.makedirs(output_file_path.parent, exist_ok=True)
    with open(output_file_path, "w") as f:
        json.dump(output_assembly_mesh.to_custom_json(), f, indent=indent)
