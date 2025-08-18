from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem import StepEigen
from ada.fem.formats.utils import DatFormatReader

from .sin2sif import convert_sin_to_sif

if TYPE_CHECKING:
    from ada.fem.results import Results
    from ada.fem.results.eigenvalue import EigenDataSummary


def get_eigen_data(lis_file: str | os.PathLike) -> EigenDataSummary:
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    dtr = DatFormatReader()

    re_compiled = dtr.compile_ff_re([int] + [float] * 3, separator=";")

    eig_str = "printofeigenvalues"
    eig_res = dtr.read_data_lines(lis_file, re_compiled, eig_str, split_data=True)
    eigen_modes: list[EigenMode] = []

    # Note! participation factors and effective modal mass are each deconstructed into 6 degrees of freedom
    for mode, eig_value, eig_freq, period in eig_res:
        eig_output = dict(
            eigenvalue=float(eig_value.replace(";", "")),
            f_hz=float(eig_freq.replace(";", "")),
        )
        eigen_modes.append(EigenMode(no=int(float(mode.replace(";", ""))), **eig_output))

    return EigenDataSummary(eigen_modes)


def read_sesam_results(results: Results, file_ref: pathlib.Path, overwrite):
    lis_file = (file_ref.parent / "SESTRA").with_suffix(".LIS")
    if lis_file.exists() and type(results.assembly.fem.steps[0]) is StepEigen:
        results.eigen_mode_data = get_eigen_data(lis_file)

    convert_sin_to_sif(results.results_file_path)

    logger.error("Result mesh data extraction is not supported for sesam")
    return None
