from __future__ import annotations

import logging
import os
import pathlib
from typing import TYPE_CHECKING, List

from ada.fem import StepEigen
from ada.fem.exceptions.fea_execution import (
    FEAnalysisUnableToStart,
    FEAnalysisUnsuccessfulError,
)
from ada.fem.formats.utils import DatFormatReader
from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

if TYPE_CHECKING:
    from ada.fem.results import Results


def get_eigen_data(dat_file: str | os.PathLike) -> EigenDataSummary:
    dtr = DatFormatReader()

    re_compiled = dtr.compile_ff_re([int] + [float] * 5)
    re_compiled_2 = dtr.compile_ff_re([int] + [float] * 6)

    eig_str = "eigenvalueoutput"
    part_str = "participationfactors"
    eff_modal = "effectivemass"

    eig_res = dtr.read_data_lines(dat_file, re_compiled, eig_str, part_str, split_data=True)
    part_res = dtr.read_data_lines(dat_file, re_compiled_2, part_str, eff_modal, split_data=True)
    modalmass = dtr.read_data_lines(dat_file, re_compiled_2, eff_modal, split_data=True)

    eigen_modes: List[EigenMode] = []

    dof_base = ["x", "y", "z", "rx", "ry", "rz"]
    part_factor_names = ["p" + x for x in dof_base]
    eff_mass_names = ["ef" + x for x in dof_base]

    # Note! participation factors and effective modal mass are each deconstructed into 6 degrees of freedom
    for eig, part, modal in zip(eig_res, part_res, modalmass):
        mode, eig_value, freq_rad, freq_cycl, gen_mass, composite_modal_damping = eig
        eig_output = dict(
            eigenvalue=eig_value,
            f_rad=freq_rad,
            f_hz=freq_cycl,
        )
        participation_data = {pn: p for pn, p in zip(part_factor_names, part[1:])}
        eff_mass_data = {pn: p for pn, p in zip(eff_mass_names, part[1:])}
        eigen_modes.append(EigenMode(no=mode, **eig_output, **participation_data, **eff_mass_data))

    return EigenDataSummary(eigen_modes)


def read_abaqus_results(results: "Results", file_ref: pathlib.Path, overwrite):
    dat_file = file_ref.with_suffix(".dat")
    if results.assembly is not None and results.assembly.fem.steps[0] == StepEigen:
        # TODO: Figure out if it is worthwhile adding support for reading step information or if it should be explicitly
        #   stated
        pass

    if dat_file.exists():
        results.eigen_mode_data = get_eigen_data(dat_file)

    check_execution(file_ref)

    logging.error("Result mesh data extraction is not supported for abaqus")
    return None


def check_execution(file_ref: pathlib.Path):
    sta_file = file_ref.with_suffix(".sta")
    if sta_file.exists() is False:
        raise FEAnalysisUnableToStart()

    with open(sta_file, "r") as f:
        if "THE ANALYSIS HAS NOT BEEN COMPLETED" in f.read():
            raise FEAnalysisUnsuccessfulError()
