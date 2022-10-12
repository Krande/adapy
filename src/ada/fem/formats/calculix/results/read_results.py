from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import meshio

from ada.fem import StepEigen

from .read_eigen_data import get_eigen_data
from .read_frd_file import read_from_frd_file
from .read_using_ccx2paraview import read_using_ccx2paraview

if TYPE_CHECKING:
    from ada.fem.results import Results

USE_FRDCONVERT = True


def read_calculix_results(results: Results, file_ref: pathlib.Path, overwrite) -> meshio.Mesh:
    if USE_FRDCONVERT:
        mesh = read_from_frd_file(file_ref)
    else:
        mesh = read_using_ccx2paraview(results, file_ref, overwrite)

    dat_file = file_ref.with_suffix(".dat")
    if results.assembly is not None and dat_file.exists() and isinstance(results.assembly.fem.steps[0], StepEigen):
        results.eigen_mode_data = get_eigen_data(dat_file)

    return mesh
