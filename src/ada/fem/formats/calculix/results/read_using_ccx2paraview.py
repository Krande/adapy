from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING

import meshio

from ada.core.file_system import get_list_of_files

if TYPE_CHECKING:
    from ada.fem.results import Results


def read_using_ccx2paraview(results: Results, file_ref: pathlib.Path, overwrite):
    try:
        from ccx2paraview.ccx2paraview import Converter
    except ModuleNotFoundError as e:
        logging.error(e)
        raise ModuleNotFoundError("ccx2paraview not found. In order to convert please install ccx2paraview first")

    result_files = get_list_of_files(file_ref.parent, ".vtu")

    if len(result_files) != 0 and overwrite is False:
        return result_files

    convert = Converter(str(file_ref), ["vtu"])
    convert.run()
    result_files = get_list_of_files(file_ref.parent, ".vtu")

    if len(result_files) == 0:
        raise FileNotFoundError("No VTU files found. Check if analysis was successfully completed")

    if len(result_files) > 1:
        logging.error("Currently only reading last step for multi-step Calculix analysis results")

    result_file = result_files[-1]
    results.results_file_path = pathlib.Path(result_file)
    print(f'Reading result from "{result_file}"')

    return meshio.read(result_file)
