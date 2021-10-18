import logging
import os
import pathlib
from typing import List, Union

import meshio
from ccx2paraview import Converter

from ada.core.utils import get_list_of_files
from ada.fem import StepEigen
from ada.fem.concepts.eigenvalue import EigenDataSummary, EigenMode
from ada.fem.formats.utils import DatFormatReader
from ada.fem.results import Results


def get_eigen_data(dat_file: Union[str, os.PathLike]) -> EigenDataSummary:
    dtr = DatFormatReader()

    re_compiled = dtr.compile_ff_re([int] + [float] * 4)
    re_compiled_2 = dtr.compile_ff_re([int] + [float] * 6)
    re_compiled_3 = dtr.compile_ff_re([float] * 6)

    eig_str = "eigenvalueoutput"
    part_str = "participationfactors"
    eff_modal = "effectivemodalmass"
    tot_eff = "totaleffectivemass"

    eig_res = dtr.read_data_lines(dat_file, re_compiled, eig_str, part_str, split_data=True)
    part_res = dtr.read_data_lines(dat_file, re_compiled_2, part_str, eff_modal, split_data=True)
    modalmass = dtr.read_data_lines(dat_file, re_compiled_2, eff_modal, tot_eff, split_data=True)
    tot_eff_mass = dtr.read_data_lines(dat_file, re_compiled_3, tot_eff, split_data=True)[0]

    dof_base = ["x", "y", "z", "rx", "ry", "rz"]
    part_factor_names = ["p" + x for x in dof_base]
    eff_mass_names = ["ef" + x for x in dof_base]

    eigen_modes: List[EigenMode] = []
    # Note! participation factors and effective modal mass are each deconstructed into 6 degrees of freedom
    for eig, part, modal in zip(eig_res, part_res, modalmass):
        mode, eig_value, freq_rad, freq_cycl, freq_imag_rad = eig
        eig_output = dict(eigenvalue=eig_value, f_rad=freq_rad, f_hz=freq_cycl, f_imag_rad=freq_imag_rad)
        participation_data = {pn: p for pn, p in zip(part_factor_names, part[1:])}
        eff_mass_data = {pn: p for pn, p in zip(eff_mass_names, part[1:])}
        eigen_modes.append(EigenMode(no=mode, **eig_output, **participation_data, **eff_mass_data))

    return EigenDataSummary(eigen_modes, tot_eff_mass)


def read_calculix_results(results: Results, file_ref: pathlib.Path, overwrite):
    result_files = get_list_of_files(file_ref.parent, ".vtu")
    if len(result_files) == 0 or overwrite is True:
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

    dat_file = file_ref.with_suffix(".dat")
    if dat_file.exists() and type(results.assembly.fem.steps[0]) == StepEigen:
        results.eigen_mode_data = get_eigen_data(dat_file)

    return meshio.read(result_file)
