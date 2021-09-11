import os
import re
from typing import List, Union

from ada.fem.concepts.eigenvalue import EigenDataSummary, EigenMode


class DatFormatReader:
    re_flags = re.MULTILINE | re.DOTALL
    re_int = r"[0-9]"
    re_decimal = r"[+|-]{0,1}[0-9].[0-9]{7}E[\+|\-][0-9]{2}"

    def compile_ff_re(self, list_of_types):
        re_str = r"^\s*("
        for t in list_of_types:
            if t is int:
                re_str += rf"{self.re_int}*\s*"
            elif t is float:
                re_str += rf"{self.re_decimal}\s*"
            else:
                raise ValueError()
        re_str += r")\n"
        return re.compile(re_str, self.re_flags)


def get_eigen_data(dat_file: Union[str, os.PathLike]) -> EigenDataSummary:
    dtr = DatFormatReader()

    re_compiled = dtr.compile_ff_re([int] + [float] * 4)
    re_compiled_2 = dtr.compile_ff_re([int] + [float] * 6)
    re_compiled_3 = dtr.compile_ff_re([float] * 6)

    tot_eff_mass: Union[List[float], None] = None
    is_curr_eig_data = False
    is_tot_eff_mass = False
    compiler = None

    eig_data = dict()
    with open(dat_file, "r") as f:
        for line in f.readlines():
            compact_str = line.replace(" ", "").strip().lower()
            if "eigenvalueoutput" in compact_str:
                is_curr_eig_data = True
                compiler = re_compiled

            if "participationfactors" in compact_str or "effectivemodalmass" in compact_str:
                is_curr_eig_data = False
                compiler = re_compiled_2

            if "totaleffectivemass" in compact_str:
                is_tot_eff_mass = True
                compiler = re_compiled_3

            if compiler is None:
                continue

            res = compiler.search(line)
            if res is not None:
                res_input = res.group(1).split()
                if is_curr_eig_data:
                    eig_data[res_input[0]] = res_input
                elif is_tot_eff_mass:
                    tot_eff_mass = [float(x) for x in res_input]
                else:
                    eig_data[res_input[0]] += res_input[1:]

        eigen_modes: List[EigenMode] = []

        for mode in eig_data.values():
            eigen_modes.append(EigenMode(*mode))

    return EigenDataSummary(eigen_modes, tot_eff_mass)
