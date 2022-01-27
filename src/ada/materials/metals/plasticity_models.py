from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

import numpy as np

if TYPE_CHECKING:
    pass


@dataclass
class PlasticityModel:
    sig_p: List[float] = field(default=None)
    eps_p: List[float] = field(default=None)


class DnvGl16Mat(PlasticityModel):
    """
    This function returns a carbon steel Material object based on the formulas in DNVGL RP C208 (september 2016)


    :param t: Thickness of the material
    :param grade: Material Grade 'S355' or 'S420'
    :param mat_def: Material Definition
    :param eps_max: Maximum epsilon
    :param data_points: Number of datapoints
    :return: tuple of (eps_p, sig_p)
    """

    @staticmethod
    def dnv_thick_select(thick, grade):
        if thick < 0.016:
            mat_str = "t_16"
        elif 0.016 <= thick < 0.04:
            mat_str = "16_t_40"
        elif 0.04 <= thick < 0.063:
            mat_str = "40_t_63"
        else:
            if grade == "S420":
                mat_str = "40_t_63"
            else:
                mat_str = "63_t_100"
        return mat_str

    def __init__(self, t, grade, mat_def="Low", eps_max=0.3, data_points=200):
        super(DnvGl16Mat, self).__init__()
        self._mat_def = mat_def
        self._grade = grade
        self._t = t

        with open(pathlib.Path(__file__).parent / "resources/NLMatParams.json", "r") as f:
            params = json.load(f)

        thick_str = self.dnv_thick_select(t, grade)
        sig_prop = float(params[mat_def][thick_str]["q_prop"][grade])
        sig_yield_1 = float(params[mat_def][thick_str]["q_yield_1"][grade])
        sig_yield_2 = float(params[mat_def][thick_str]["q_yield_2"][grade])
        ep_y1 = float(params[mat_def][thick_str]["ep_y1"][grade])
        ep_y2 = float(params[mat_def][thick_str]["ep_y2"][grade])
        n = float(params[mat_def][thick_str]["n"][grade])
        K = float(params[mat_def][thick_str]["K"][grade])

        eps = np.linspace(ep_y2, eps_max, num=data_points)

        ep = list()
        ep.append(0.0)
        ep.append(ep_y1)
        ep.append(ep_y2)
        sig = list()
        sig.append(sig_prop)
        sig.append(sig_yield_1)
        sig.append(sig_yield_2)

        init_val = 0
        for e in eps:
            if init_val == 0:
                init_val = 1
            else:
                ep.append(e)
                sig.append(K * (e + (sig_yield_2 / K) ** (1.0 / n) - ep_y2) ** n)

        self.eps_p = ep
        self.sig_p = sig

    def __repr__(self):
        return f"Dnvgl16Mat({self.nl_name})"

    @property
    def nl_name(self):
        mat_prefix = "RP"
        mat_def_str = "L" if self._mat_def == "Low" else "M"
        return mat_prefix + "_" + self._grade + mat_def_str + "_" + self.dnv_thick_select(self._t, self._grade)

    @property
    def metadata(self):
        return dict(description="Carbon Steel nonlinear material based on DNVGL-RP-C208 (Sept. 2019)")
