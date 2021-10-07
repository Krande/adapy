from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
from pydantic import validate_arguments


@dataclass
class EigenDataSummary:
    modes: List[EigenMode]
    tot_eff_mass: List[float] = None

    def calc_tot_eff_mass(self):
        tem = np.zeros(6)
        for m in self.modes:
            tem[0] += m.efx
            tem[1] += m.efy
            tem[2] += m.efz
            tem[3] += m.efrx
            tem[4] += m.efry
            tem[5] += m.efrz
        return tem.tolist()


@validate_arguments
@dataclass
class EigenMode:
    no: int
    f_hz: np.float64 = field(default=None, repr=True)
    eigenvalue: np.float64 = field(default=None, repr=False)
    f_rad: np.float64 = field(default=None, repr=False)
    f_imag_rad: np.float64 = field(default=None, repr=False)

    # Participation factors
    px: np.float64 = field(default=None, repr=False)
    py: np.float64 = field(default=None, repr=False)
    pz: np.float64 = field(default=None, repr=False)
    prx: np.float64 = field(default=None, repr=False)
    pry: np.float64 = field(default=None, repr=False)
    prz: np.float64 = field(default=None, repr=False)

    # Effective Modal Mass
    efx: np.float64 = field(default=None, repr=False)
    efy: np.float64 = field(default=None, repr=False)
    efz: np.float64 = field(default=None, repr=False)
    efrx: np.float64 = field(default=None, repr=False)
    efry: np.float64 = field(default=None, repr=False)
    efrz: np.float64 = field(default=None, repr=False)


def eig_data_to_df(eig_data: EigenDataSummary, columns: List[str]):
    """Convert EigenDataSummary to a pandas Dataframe (assumes you have pandas installed)"""
    try:
        import pandas as pd
    except ModuleNotFoundError:
        raise ModuleNotFoundError('Pandas not installed. Use "conda install -c conda-forge pandas" to install.')

    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)
