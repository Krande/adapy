from __future__ import annotations

import os
import pathlib
import re
from typing import TYPE_CHECKING, List

from ada.config import logger
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
        participation_data = {pn: float(p) for pn, p in zip(part_factor_names, part[1:])}
        eff_mass_data = {pn: float(p) for pn, p in zip(eff_mass_names, modal[1:])}
        eigen_modes.append(EigenMode(no=int(float(mode)), **eig_output, **participation_data, **eff_mass_data))

    tot_eff_mass = [float(x) for x in tot_eff_mass]
    scale = _participation_scale(pathlib.Path(dat_file).with_suffix(".inp"))
    if scale != 1.0:
        for em in eigen_modes:
            for name in part_factor_names:
                setattr(em, name, getattr(em, name) / scale)
            for name in eff_mass_names:
                setattr(em, name, getattr(em, name) / scale**2)
        tot_eff_mass = [x / scale**2 for x in tot_eff_mass]

    return EigenDataSummary(eigen_modes, tot_eff_mass)


# CalculiX (2.23) expands an S4 shell into a C3D8I solid and reports the
# participation factors of such a model 3x too large -- effective masses and
# their totals 9x. The frequencies and mode shapes are right. Checked against
# the analytic clamped plate (mode 1 effective mass 0.613 of the plate mass)
# and against S4R / Abaqus / Sesam / Code_Aster on the verification
# cantilever. The other shells (S3, S4R, S6, S8, S8R) report correctly.
S4_PARTICIPATION_SCALE = 3.0

_ELEMENT_TYPE_RE = re.compile(r"^\*ELEMENT\s*,[^\n]*?\bTYPE\s*=\s*(\w+)", re.IGNORECASE | re.MULTILINE)


def _participation_scale(inp_file: pathlib.Path) -> float:
    """The factor CalculiX's participation factors are off by for this deck.

    Only a model of S4 shells alone is corrected: mixed with other elements
    the error is no longer a single factor, so the values are kept as
    reported and a warning is logged."""
    if not inp_file.is_file():
        return 1.0
    el_types = {t.upper() for t in _ELEMENT_TYPE_RE.findall(inp_file.read_text(errors="ignore"))}
    if "S4" not in el_types:
        return 1.0
    if el_types == {"S4"}:
        return S4_PARTICIPATION_SCALE
    logger.warning(
        f"{inp_file.name}: CalculiX reports S4 participation factors 3x too large; "
        f"mixed with {sorted(el_types - {'S4'})} that cannot be corrected, values are kept as reported"
    )
    return 1.0
