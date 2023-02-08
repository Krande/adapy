from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class EigenDataSummary:
    modes: list[EigenMode]
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

    def to_dict(self) -> dict:
        res = dict()
        for m in self.modes:
            key = m.no if isinstance(m.no, np.int32) is False else int(m.no)
            res[key] = m.to_dict()
        return res

    def from_dict(self, values_dict: dict):
        for no in sorted(values_dict.keys(), key=int):
            self.modes.append(EigenMode(int(no), source_dict=values_dict[no]))


@dataclass
class EigenMode:
    no: int
    f_hz: np.float64 | float = field(default=None, repr=True)
    eigenvalue: np.float64 | float = field(default=None, repr=False)
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

    source_dict: dict = field(default=None, repr=False)

    def __post_init__(self):
        if self.source_dict is not None:
            for key, value in self.source_dict.items():
                setattr(self, key, value)

    def to_dict(self):
        res = dict()
        for key, value in self.__dict__.items():
            if key[0] == "_":
                continue
            if isinstance(value, np.int32):
                value = int(value)
            elif isinstance(value, np.float64):
                value = float(value)
            res[key] = value
        return res


def eig_data_to_df(eig_data: EigenDataSummary, columns: List[str]):
    """Convert EigenDataSummary to a pandas Dataframe (assumes you have pandas installed)"""
    try:
        import pandas as pd
    except ModuleNotFoundError:
        raise ModuleNotFoundError('Pandas not installed. Use "conda install -c conda-forge pandas" to install.')

    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)
