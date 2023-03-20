from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import meshio

from ada.fem import StepEigen

from .read_eigen_data import get_eigen_data

if TYPE_CHECKING:
    from ada.fem.results import Results

USE_FRDCONVERT = True


def read_calculix_results(results: Results, file_ref: pathlib.Path, overwrite) -> meshio.Mesh:
    from .read_frd_file import read_from_frd_file

    mesh = read_from_frd_file(file_ref)

    dat_file = file_ref.with_suffix(".dat")
    if results.assembly is not None and dat_file.exists() and isinstance(results.assembly.fem.steps[0], StepEigen):
        results.eigen_mode_data = get_eigen_data(dat_file)

    return mesh
