from __future__ import annotations

import os
from typing import TYPE_CHECKING, List

from ada.fem.formats.utils import DatFormatReader

if TYPE_CHECKING:
    from ada.fem.results.eigenvalue import EigenDataSummary


def get_eigen_data(dat_file: str | os.PathLike) -> EigenDataSummary:
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

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
        eig_output = dict(
            eigenvalue=float(eig_value),
            f_rad=float(freq_rad),
            f_hz=float(freq_cycl),
            f_imag_rad=float(freq_imag_rad),
        )
        participation_data = {pn: p for pn, p in zip(part_factor_names, part[1:])}
        eff_mass_data = {pn: p for pn, p in zip(eff_mass_names, part[1:])}
        eigen_modes.append(EigenMode(no=mode, **eig_output, **participation_data, **eff_mass_data))

    return EigenDataSummary(eigen_modes, tot_eff_mass)
