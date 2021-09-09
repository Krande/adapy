from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
from pydantic import validate_arguments


@dataclass
class EigenDataSummary:
    modes: List[EigenMode]
    tot_eff_mass: List[float] = None


@validate_arguments
@dataclass
class EigenMode:
    no: int
    eigenvalue: np.float64
    frequency: np.float64 = field(default=None, repr=False)
    real: np.float64 = field(default=None, repr=True)
    imaginary: np.float64 = field(default=None, repr=False)

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
