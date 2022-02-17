import logging
import pathlib
from typing import TYPE_CHECKING

import meshio

from ada.fem import StepEigen

from .read_eigen_data import get_eigen_data
from .read_using_ccx2paraview import read_using_ccx2paraview

if TYPE_CHECKING:
    from ada.fem.results import Results


def read_calculix_results(results: "Results", file_ref: pathlib.Path, overwrite):
    result_files = read_using_ccx2paraview(file_ref, overwrite)

    if len(result_files) == 0:
        raise FileNotFoundError("No VTU files found. Check if analysis was successfully completed")

    if len(result_files) > 1:
        logging.error("Currently only reading last step for multi-step Calculix analysis results")

    result_file = result_files[-1]
    results.results_file_path = pathlib.Path(result_file)
    print(f'Reading result from "{result_file}"')

    dat_file = file_ref.with_suffix(".dat")
    if dat_file.exists() and type(results.assembly.fem.steps[0]) == StepEigen:
        results.eigen_mode_data = get_eigen_data(dat_file)

    return meshio.read(result_file)
