import os
import re
from typing import List, Union

import numpy as np

from ada.fem.concepts.eigenvalue import EigenDataSummary, EigenMode

re_flags = re.MULTILINE | re.DOTALL

re_eig_dat_file = re.compile(r"\(CYCLES\/TIME\(RAD\/TIME\)(.*)", re_flags)
re_eig_mass_part = re.compile(r"Z-ROTATION(.*)", re_flags)
re_eff_modal_mass = re.compile(r"Z-ROTATION(.*)", re_flags)
re_tot_eff_mass = re.compile(r"Z-ROTATION(.*)", re_flags)

eig_h_str = "E I G E N V A L U E   O U T P U T"
part_fact_h_str = "P A R T I C I P A T I O N   F A C T O R S"
eff_mod_mass_h_str = "E F F E C T I V E   M O D A L   M A S S"
tot_eff_mass_h_str = "T O T A L   E F F E C T I V E   M A S S"


def get_eig_from_dat_file(dat_file: Union[str, os.PathLike]) -> EigenDataSummary:
    eigen_modes: List[EigenMode] = []

    with open(dat_file, "r") as f:
        bulk_str = f.read()
        mode_data, tot_eff_mass = extract_eig_data_from_bulk_str(bulk_str)
        for eig_m, eig_pm, eig_mm in mode_data:
            eigen_modes.append(EigenMode(*eig_m, *eig_pm[1:], *eig_mm[1:]))

    return EigenDataSummary(eigen_modes, tot_eff_mass)


def extract_eig_data_from_bulk_str(bulk_str):
    e2, e3, e4 = bulk_str.rfind(part_fact_h_str), bulk_str.rfind(eff_mod_mass_h_str), bulk_str.find(tot_eff_mass_h_str)
    m_eig = re_eig_dat_file.search(bulk_str[:e2])
    m_part_mass = re_eig_mass_part.search(bulk_str[e2:e3])
    m_eff_modal_mass = re_eff_modal_mass.search(bulk_str[e3:])
    tot_effe_mass = re_tot_eff_mass.search(bulk_str[e4:])

    def to_list(x):
        return (line.split() for line in x.group(1).strip().splitlines())

    eig_main = to_list(m_eig)
    eig_part_mass = to_list(m_part_mass)
    eig_modal_mass = to_list(m_eff_modal_mass)

    tot_eff_mass = [np.float64(x) for x in tot_effe_mass.group(1).strip().split()]

    return zip(eig_main, eig_part_mass, eig_modal_mass), tot_eff_mass
