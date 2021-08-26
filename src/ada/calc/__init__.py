from typing import Union

import numpy as np
from IPython.display import display
from ipywidgets import VBox

from ada import Beam
from ada.core.utils import Counter
from ada.visualize.plots import easy_plotly


class ResType:
    displ = "DISPL"
    moment = "MOMENT"
    shear = "SHEAR"


def shear(x, w, L):
    """

    :param x:
    :param w:
    :param L:
    :eq: $$V_x(x) = w({L \\over 2} - x)$$

    :return:
    """
    return w * (L / 2 - x)


def displ(x, w, E, I, L):
    """
    Displacement equation

    :param x:
    :param w:
    :param E:
    :param I:
    :param L:
    :return:

    :eq: $$\\Delta_x=\\frac{wx}{24EI}(L^3-2Lx^2+x^3)$$

    """
    return w * x * (L ** 3 - 2 * L * x ** 2 + x ** 3) / (24 * E * I)


def moment(x, w, L):
    return w * x * (L - x) / 2


class BeamCalc:
    def __init__(self, beam: Beam):
        """

        :param beam:
        """
        self._beam = beam
        self._w = []
        self._p = []
        self._pnames = Counter(prefix="p-load-")
        self._wnames = Counter(prefix="w-load-")

    def add_distributed_load(self, w, x_start=None, x_end=None, name=None, use_relative_position=True):
        name = name if name is not None else next(self._pnames)
        self._w.append((name, w, x_start, x_end, use_relative_position))

    def add_point_load(self, p, x, name=None, use_relative_position=True):
        """

        :param p: Load Magnitude
        :param x: Position of load
        :param name: Give the load a unique name
        :param use_relative_position: If relative position of load is given with [0, 1], alternative is absolute pos
        from beginning of beam
        :return:
        """
        name = name if name is not None else next(self._pnames)
        self._p.append((name, p, x, use_relative_position))

    def get_displ_formula(self):
        return equation_compiler(displ, True)

    def get_shear_formula(self):
        return equation_compiler(shear, True)

    def get_moment_formula(self):
        return equation_compiler(moment, True)

    def _repr_html_(self):
        from ada.config import Settings

        # Create Plotly diagrams for displacement, shear and moment UDL's
        dt_points = 50
        displacements = np.zeros((2, dt_points))
        shear_ = np.zeros((2, dt_points))
        moment_ = np.zeros((2, dt_points))

        # Make sure midpoint is represented in datapoints
        l_half = self._beam.length / 2
        p_half = int(dt_points / 2)
        data_points_1 = np.linspace(0, l_half, p_half, endpoint=True)
        data_points = np.concatenate([data_points_1, np.linspace(l_half, self._beam.length, p_half, endpoint=True)])
        if len(self._w) > 0:
            for w in self._w:
                # Displacements
                displ_res = [(x, simply_supported(x, w[1], self._beam, ResType.displ)) for x in data_points]
                res_np = np.array(list(zip(*displ_res)))
                displacements += res_np
                # Moment
                moment_res = [(x, simply_supported(x, w[1], self._beam, ResType.moment)) for x in data_points]
                res_np = np.array(list(zip(*moment_res)))
                moment_ += res_np
                # Shear
                shear_res = [(x, simply_supported(x, w[1], self._beam, ResType.shear)) for x in data_points]
                res_np = np.array(list(zip(*shear_res)))
                shear_ += res_np

        l_displ = displacements.tolist()
        l_shear = shear_.tolist()
        l_moment = moment_.tolist()

        plot_displ = easy_plotly(
            "Simply Support Beam (displacements)",
            l_displ,
            xlbl="Beam Length [m]",
            ylbl="Displacement [m]",
            return_widget=True,
        )
        plot_shear = easy_plotly(
            "Simply Support Beam (shear)",
            l_shear,
            xlbl="Beam Length [m]",
            ylbl="Shear [N]",
            return_widget=True,
        )
        plot_moment = easy_plotly(
            "Simply Support Beam (moments)",
            l_moment,
            xlbl="Beam Length [m]",
            ylbl="Moment [Nm]",
            return_widget=True,
        )
        if Settings.silence_display is True:
            display(VBox([plot_displ, plot_moment, plot_shear]))
        return ""


def simply_supported(x, w, beam: Beam, res_type: Union[ResType.displ, ResType.shear, ResType.moment] = ResType.displ):
    """
    Simply Supported beam displacement function

    :param x: Point along length of beam
    :param w: Magnitude of distributed load
    :param beam: Ada beam
    :param res_type: Result type

    :return:
    """
    E = beam.material.model.E
    I = beam.section.properties.Iy
    L = beam.length
    res_map = dict(
        DISPL=displ(x, w, E, I, L),
        MOMENT=moment(x, w, L),
        SHEAR=shear(x, w, L),
    )
    f = res_map[res_type]

    # f_str = equation_compiler(displ)

    return f


def equation_compiler(f, print_latex=False, print_formula=False):
    from inspect import getsourcelines

    try:
        import pytexit
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "To use the equation compiler you will need to install pytexit first.\n"
            'Use "pip install pytexit"\n\n'
            f'Original error message: "{e}"'
        )

    lines = getsourcelines(f)
    final_line = lines[0][-1]
    return pytexit.py2tex(final_line.replace("return ", ""), print_latex=print_latex, print_formula=print_formula)
