# coding=utf-8
import numpy as np


class Metal:
    """
    Base object for all metals
    """

    def __init__(
        self,
        E,
        rho,
        sig_y,
        sig_u,
        v,
        zeta,
        alpha,
        plasticitymodel,
        eps_p=None,
        sig_p=None,
        units="m",
    ):
        self._E = E
        self._rho = rho
        self._sig_y = sig_y
        self._sig_u = sig_u
        self._v = v
        self.zeta = zeta
        self._alpha = alpha
        self._plasticitymodel = plasticitymodel
        self._eps_p = eps_p
        self._sig_p = sig_p
        self._units = units

    def __delattr__(self, item):
        raise AttributeError("Deletion of base material object properties is not allowed!")

    # def __setattr__(self, key, value):
    #     if key in self.__dict__:
    #         raise AttributeError("Can't set attribute {!r} on base material object".format(key))
    #     self.__dict__[key] = value

    def __repr__(self):
        return f"Metal(E:{self.E}, rho:{self.rho}, Sigy: {self.sig_y}, Plasticity Model: {self.plasticity_model})"

    @property
    def E(self):
        """

        :return: Young's Modulus
        """
        return self._E

    @E.setter
    def E(self, value):
        if value < 0:
            raise ValueError("The Young's Modulus must be a positive number")
        self._E = value

    @property
    def sig_y(self):
        """
        Yield stress

        :return:
        """
        return self._sig_y

    @sig_y.setter
    def sig_y(self, value):
        if value < 0:
            raise ValueError("Yield Stress must be a positive number")
        self._sig_y = value

    @property
    def sig_u(self):
        """
        :return: Ultimate yield stress
        """
        return self._sig_u

    @property
    def rho(self):
        """
        :return: Density
        """
        return self._rho

    @property
    def v(self):
        """
        Poisson Ratio

        :return:
        """
        return self._v

    @property
    def alpha(self):
        """
        Thermal Expansion coefficient

        :return:
        """
        return self._alpha

    @property
    def zeta(self):
        """
        Material damping coefficient

        :return:
        """
        return self._zeta

    @zeta.setter
    def zeta(self, value):
        if value is None or value < 0.0:
            raise ValueError("Zeta cannot be None or below zero")
        self._zeta = value

    @property
    def plasticity_model(self):
        """

        :return: Constitutive Equation for plasticity
        """
        return self._plasticitymodel

    @property
    def eps_p(self):
        if self._eps_p is not None:
            return self._eps_p
        elif self._plasticitymodel is not None:
            return self._plasticitymodel.eps_p
        else:
            return None

    @property
    def sig_p(self):
        if self._sig_p is not None:
            return self._sig_p
        elif self._plasticitymodel is not None:
            return self._plasticitymodel.sig_p
        else:
            return None

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
    def cp(self):
        return None

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units == "m" and value == "mm":
            self._E *= 1e-6
            if self._sig_y is not None:
                self._sig_y *= 1e-6
            self._rho *= 1e-9
            self._units = value
        elif self._units == "mm" and value == "m":
            self._E *= 1e6
            if self._sig_y is not None:
                self._sig_y *= 1e6
            self._rho *= 1e9
            self._units = value


class DNVGL16PBase:
    def __init__(self, q_prop, q_yield_1, q_yield_2, ep_y1, ep_y2, n, K):
        self.q_prop = q_prop
        self.q_yield_1 = q_yield_1
        self.q_yield_2 = q_yield_2
        self.ep_y1 = ep_y1
        self.ep_y2 = ep_y2
        self.n = n
        self.K = K


class DnvGl16Mat:
    """
    This function returns a carbon steel Material object based on the formulas in DNVGL RP C208 (september 2016)


    :param t: Thickness of the material
    :param grade: Material Grade 'S355' or 'S420'
    :param mat_def: Material Definition
    :param eps_max: Maximum epsilon
    :param data_points: Number of datapoints
    :return: tuple of (eps_p, sig_p)
    """

    _params = {
        "Low": {
            "t_16": {
                "q_prop": {"S355": 320.0e6, "S420": 378.7e6},
                "q_yield_1": {"S355": 357.0e6, "S420": 422.5e6},
                "q_yield_2": {"S355": 366.1e6, "S420": 426.3e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.012},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 740e6, "S420": 738e6},
            },
            "16_t_40": {
                "q_prop": {"S355": 311.0e6, "S420": 360.6e6},
                "q_yield_1": {"S355": 346.9e6, "S420": 402.4e6},
                "q_yield_2": {"S355": 355.9e6, "S420": 406.0e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.012},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 740e6, "S420": 703e6},
            },
            "40_t_63": {
                "q_prop": {"S355": 301.9e6, "S420": 351.6e6},
                "q_yield_1": {"S355": 336.9e6, "S420": 392.3e6},
                "q_yield_2": {"S355": 345.7e6, "S420": 395.9e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.012},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 725e6, "S420": 686e6},
            },
            "63_t_100": {
                "q_prop": {"S355": 284e6},
                "q_yield_1": {"S355": 316.7e6},
                "q_yield_2": {"S355": 323.8e6},
                "ep_y1": {"S355": 0.004},
                "ep_y2": {"S355": 0.015},
                "n": {"S355": 0.166},
                "K": {"S355": 725e6},
            },
        },
        "Mean": {
            "t_16": {
                "q_prop": {"S355": 384.0e6, "S420": 435.5e6},
                "q_yield_1": {"S355": 428.4e6, "S420": 482.9e6},
                "q_yield_2": {"S355": 439.3e6, "S420": 487.2e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.011928571},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 900e6, "S420": 738e6},
            },
            "16_t_40": {
                "q_prop": {"S355": 357.7e6, "S420": 432.7e6},
                "q_yield_1": {"S355": 398.9e6, "S420": 482.9e6},
                "q_yield_2": {"S355": 409.3e6, "S420": 487.2e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.011929},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 850e6, "S420": 703e6},
            },
            "40_t_63": {
                "q_prop": {"S355": 332.1e6, "S420": 421.9e6},
                "q_yield_1": {"S355": 370.6e6, "S420": 470.8e6},
                "q_yield_2": {"S355": 380.3e6, "S420": 475.1e6},
                "ep_y1": {"S355": 0.004, "S420": 0.004},
                "ep_y2": {"S355": 0.015, "S420": 0.011929},
                "n": {"S355": 0.166, "S420": 0.14},
                "K": {"S355": 800e6, "S420": 686e6},
            },
            "63_t_100": {
                "q_prop": {"S355": 312.4e6},
                "q_yield_1": {"S355": 348.4e6},
                "q_yield_2": {"S355": 350.6e6},
                "ep_y1": {"S355": 0.004},
                "ep_y2": {"S355": 0.015},
                "n": {"S355": 0.166},
                "K": {"S355": 800e6},
            },
        },
    }

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
        self._mat_def = mat_def
        self._grade = grade
        self._t = t
        thick_str = self.dnv_thick_select(t, grade)
        sig_prop = float(self._params[mat_def][thick_str]["q_prop"][grade])
        sig_yield_1 = float(self._params[mat_def][thick_str]["q_yield_1"][grade])
        sig_yield_2 = float(self._params[mat_def][thick_str]["q_yield_2"][grade])
        ep_y1 = float(self._params[mat_def][thick_str]["ep_y1"][grade])
        ep_y2 = float(self._params[mat_def][thick_str]["ep_y2"][grade])
        n = float(self._params[mat_def][thick_str]["n"][grade])
        K = float(self._params[mat_def][thick_str]["K"][grade])

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

        self._eps_p = ep
        self._sig_p = sig

    def __repr__(self):
        return f"Dnvgl16Mat({self.nl_name})"

    @property
    def eps_p(self):
        return self._eps_p

    @property
    def sig_p(self):
        return self._sig_p

    @property
    def nl_name(self):
        mat_prefix = "RP"
        mat_def_str = "L" if self._mat_def == "Low" else "M"
        return mat_prefix + "_" + self._grade + mat_def_str + "_" + self.dnv_thick_select(self._t, self._grade)

    @property
    def metadata(self):
        return dict(description="Carbon Steel nonlinear material based on DNVGL-RP-C208 (Sept. 2019)")


class CarbonSteel(Metal):
    GRADES = dict(
        S355=dict(name="S355", sigy=355e6, sigu=355e6),
        S420=dict(name="S420", sigy=420e6, sigu=420e6),
    )
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
    :param eps_p: Plastic strain
    :param sig_p: Plastic stress
    :param temp_range: Temperature range
    :param units: Definition of length unit. Default is meter 'm'. Alternative is millimeter 'mm'.
    """

    def __init__(
        self,
        grade="S355",
        plasticity_model=DnvGl16Mat(t=0.01, grade="S355", mat_def="Low", eps_max=0.3, data_points=200),
        E=2.1e11,
        rho=7850,
        v=0.3,
        zeta=1.15,
        alpha=1.2e-5,
        sig_y=None,
        sig_u=None,
        eps_p=None,
        sig_p=None,
        temp_range=None,
        units="m",
    ):
        self._grade = grade
        sig_y = sig_y if sig_y is not None else CarbonSteel.GRADES[grade]["sigy"]
        sig_u = sig_u if sig_u is not None else CarbonSteel.GRADES[grade]["sigu"]

        Metal.__init__(
            self,
            E,
            rho,
            sig_y,
            sig_u,
            v,
            zeta,
            alpha,
            plasticity_model,
            sig_p=sig_p,
            eps_p=eps_p,
            units=units,
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
        """
        Thermal conductivity

        watts per meter-kelvin W/(mK)

        :return:
        """

        phase1_end = 780
        phase1_arr = [self._temp_range[x] for x in np.where(self._temp_range <= phase1_end)]
        phase2_arr = [self._temp_range[x] for x in np.where(self._temp_range > phase1_end)]
        phase1 = [54 - t * 3.33 * 0.01 for t in phase1_arr[0]]
        phase2 = [27.3 for x in range(phase2_arr[0].shape[0])]
        return phase1 + phase2

    @property
    def cp(self):
        """
        Specific Heat

        joule per kelvin and kilogram J/(K kg)

        :return:
        """

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
        phase1 = [425 + 7.73 * 1e-1 * t - 1.69 * 1e-3 * (t ** 2) + 2.22 * 1e-6 * t ** 3 for t in phase1_arr[0]]
        phase2 = [666 + 13002 / (738 - t) for t in phase2_arr[0]]
        phase3 = [545 + 17820 / (t - 731) for t in phase3_arr[0]]
        phase4 = [650 for x in range(phase4_arr[0].shape[0])]
        return phase1 + phase2 + phase3 + phase4


class Aluminium(Metal):
    def __init__(self):
        raise NotImplementedError("The aluminium material model is not yet implemented")
