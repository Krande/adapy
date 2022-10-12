# coding=utf-8
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ada.base.units import Units

if TYPE_CHECKING:
    from ada import Material

from .plasticity_models import PlasticityModel


@dataclass
class MaterialDampingRayleigh:
    alpha: float = field(default=None, init=False)
    beta: float = field(default=None, init=False)


class Metal:
    """Base object for all metals"""

    def __init__(self, E, rho, sig_y, sig_u, v, zeta, alpha, plasticitymodel=PlasticityModel(), units="m", parent=None):
        self._E = E
        self._rho = rho
        self._sig_y = sig_y
        self._sig_u = sig_u
        self._v = v
        self.zeta = zeta
        self._alpha = alpha
        self._plasticity_model = plasticitymodel
        self._units = units
        self._rayleigh_damping = MaterialDampingRayleigh()
        self._parent = parent

    def __delattr__(self, item):
        raise AttributeError("Deletion of base material object properties is not allowed!")

    def __eq__(self, other: Metal):
        for att in filter(lambda x: x.startswith("__") is False and not callable(getattr(self, x)), dir(self)):
            self_var = getattr(self, att)
            other_var = getattr(other, att)
            if att in ["GRADES", "EC3_E_RED", "EC3_S_RED", "EC3_TEMP"]:
                continue
            if type(self_var) in (float, str, int) or self_var is None:
                return self_var == other_var
            elif type(self_var) in (list,):
                return all([x == y for x, y in zip(self_var, other_var)])
            elif type(self_var) in (np.ndarray,):
                comparison = self_var == other_var
                return comparison.all()
            else:
                raise NotImplementedError()

        return True

    def equal_props(self, other: Metal):
        for pa, pb in zip(self.unique_props(), other.unique_props()):
            if pa != pb:
                return False

        return True

    def unique_props(self):
        props = [
            "E",
            "sig_y",
            "sig_u",
            "rho",
            "v",
            "alpha",
            "zeta",
            "plasticity_model",
            "E_therm",
            "sigy_therm",
            "kappa",
            "rayleigh_damping",
            "cp",
        ]
        return [getattr(self, p) for p in props]

    def __repr__(self):
        return f"Metal(E:{self.E}, rho:{self.rho}, Sigy: {self.sig_y}, Plasticity Model: {self.plasticity_model})"

    @property
    def E(self):
        """Young's Modulus"""
        return self._E

    @E.setter
    def E(self, value):
        if value < 0:
            raise ValueError("The Young's Modulus must be a positive number")
        self._E = value

    @property
    def G(self):
        """Shear Modulus"""
        return self.E / (2 * (1 + self.v))

    @property
    def sig_y(self):
        """Yield stress"""
        return self._sig_y

    @sig_y.setter
    def sig_y(self, value):
        if value < 0:
            raise ValueError("Yield Stress must be a positive number")
        self._sig_y = value

    @property
    def sig_u(self):
        """Ultimate yield stress"""
        return self._sig_u

    @property
    def rho(self) -> float:
        """Density"""
        return self._rho

    @rho.setter
    def rho(self, value: float):
        if value < 0.0:
            raise ValueError("Material density must be a positive number")
        self._rho = value

    @property
    def v(self) -> float:
        """Poisson Ratio"""
        return self._v

    @property
    def alpha(self) -> float:
        """Thermal Expansion coefficient"""
        return self._alpha

    @property
    def zeta(self) -> float:
        """Material damping coefficient"""
        return self._zeta

    @zeta.setter
    def zeta(self, value: float):
        if value is None or value < 0.0:
            raise ValueError("Zeta cannot be None or below zero")
        self._zeta = value

    @property
    def plasticity_model(self) -> PlasticityModel:
        """Constitutive Equation for plasticity"""
        return self._plasticity_model

    @plasticity_model.setter
    def plasticity_model(self, value: PlasticityModel):
        self._plasticity_model = value

    @property
    def E_therm(self):
        return None

    @property
    def sigy_therm(self):
        return None

    @property
    def kappa(self):
        return None

    @property
    def rayleigh_damping(self):
        return self._rayleigh_damping

    @property
    def cp(self):
        return None

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units == Units.M and value == Units.MM:
            self._E *= 1e-6
            if self._sig_y is not None:
                self._sig_y *= 1e-6
            self._rho *= 1e-9
            self._units = value
        elif self._units == Units.MM and value == Units.M:
            self._E *= 1e6
            if self._sig_y is not None:
                self._sig_y *= 1e6
            self._rho *= 1e9
            self._units = value

    @property
    def parent(self) -> "Material":
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


class CarbonSteelGradeTypes:
    S355 = "S355"
    S420 = "S420"


class CarbonSteel(Metal):
    TYPES = CarbonSteelGradeTypes
    GRADES = {
        TYPES.S355: dict(name="S355", sigy=355e6, sigu=355e6),
        TYPES.S420: dict(name="S420", sigy=420e6, sigu=420e6),
    }
    EC3_TEMP = [20, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]
    EC3_E_RED = [
        1.0,
        1.0,
        0.9,
        0.8,
        0.7,
        0.6,
        0.31,
        0.13,
        0.09,
        0.0675,
        0.045,
        0.0225,
        0.0,
    ]
    EC3_S_RED = [1.0, 1.0, 1.0, 1.0, 1.0, 0.78, 0.47, 0.23, 0.11, 0.06, 0.04, 0.02, 0.0]

    def __init__(
        self,
        grade="S355",
        plasticity_model: PlasticityModel = None,
        E=2.1e11,
        rho=7850,
        v=0.3,
        zeta=1.15,
        alpha=1.2e-5,
        sig_y=None,
        sig_u=None,
        temp_range=None,
        units="m",
        parent=None,
    ):
        """
        :param grade: Material Grade
        :param plasticity_model: Plasticity model e.g. CarbonSteel
        :param E: Young's Modulus
        :param rho: Material Density
        :param v: Poisson Ratio
        :param zeta: Material damping coefficient
        :param alpha: Thermal Expansion coefficient
        :param sig_y: Yield stress
        :param sig_u: Ultimate stress
        :param temp_range: Temperature range
        :param units: Definition of length unit. Default is meter 'm'. Alternative is millimeter 'mm'.
        """
        self._grade = grade
        sig_y = sig_y if sig_y is not None else CarbonSteel.GRADES[grade]["sigy"]
        sig_u = sig_u if sig_u is not None else CarbonSteel.GRADES[grade]["sigu"]
        super(CarbonSteel, self).__init__(
            E=E,
            rho=rho,
            sig_y=sig_y,
            sig_u=sig_u,
            v=v,
            zeta=zeta,
            alpha=alpha,
            plasticitymodel=plasticity_model,
            units=units,
            parent=parent,
        )
        # Manually override variables

        self._temp_range = np.arange(20, 1210, 5) if temp_range is None else temp_range

    def __repr__(self):
        return (
            f"CarbonSteel(E:{self.E:.3E}, sig_y:{self.sig_y:.3E}, rho:{self.rho:.3E},"
            f" plasticity_model:{self.plasticity_model})"
        )

    @property
    def grade(self):
        """Material Grade"""
        return self._grade

    @property
    def temp_range(self):
        return self._temp_range

    @property
    def E_therm(self):
        E_red_fac = np.interp(self._temp_range, CarbonSteel.EC3_TEMP, CarbonSteel.EC3_E_RED)
        return [self._E * x for x in E_red_fac]

    @property
    def sigy_therm(self):
        sig_red_fac = np.interp(self._temp_range, CarbonSteel.EC3_TEMP, CarbonSteel.EC3_S_RED)
        return [self._sig_y * x for x in sig_red_fac]

    @property
    def kappa(self):
        """Thermal conductivity. Watts per meter-kelvin W/(mK)"""
        phase1_end = 780
        phase1_arr = [self._temp_range[x] for x in np.where(self._temp_range <= phase1_end)]
        phase2_arr = [self._temp_range[x] for x in np.where(self._temp_range > phase1_end)]
        phase1 = [54 - t * 3.33 * 0.01 for t in phase1_arr[0]]
        phase2 = [27.3 for x in range(phase2_arr[0].shape[0])]
        return phase1 + phase2

    @property
    def cp(self):
        """Specific Heat. Joule per kelvin and kilogram J/(K kg)"""

        phase1_end = 600
        phase2_end = 735
        phase3_end = 900
        phase1_arr = [self._temp_range[x] for x in np.where(self._temp_range <= phase1_end)]
        phase2_arr = [
            self._temp_range[x]
            for x in np.where(np.logical_and(self._temp_range > phase1_end, self._temp_range <= phase2_end))
        ]
        phase3_arr = [
            self._temp_range[x]
            for x in np.where(np.logical_and(self._temp_range > phase2_end, self._temp_range <= phase3_end))
        ]
        phase4_arr = [self._temp_range[x] for x in np.where(self._temp_range > phase3_end)]
        phase1 = [425 + 7.73 * 1e-1 * t - 1.69 * 1e-3 * (t**2) + 2.22 * 1e-6 * t**3 for t in phase1_arr[0]]
        phase2 = [666 + 13002 / (738 - t) for t in phase2_arr[0]]
        phase3 = [545 + 17820 / (t - 731) for t in phase3_arr[0]]
        phase4 = [650 for x in range(phase4_arr[0].shape[0])]
        return phase1 + phase2 + phase3 + phase4


class Aluminium(Metal):
    def __init__(self):
        raise NotImplementedError("The aluminium material model is not yet implemented")
