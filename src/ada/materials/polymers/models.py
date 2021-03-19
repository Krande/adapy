import inspect

import ipywidgets as widgets
import numpy as np
import plotly.graph_objs as go
import scipy.optimize
from IPython.display import display


class Calibrate(object):
    """


    :param mat_data: Material data sheet object
    :param interpolate: All test dataset arrays are interpolated to have equal length.
    :param num_int: Total number of points employed in the linear interpolation of test strain and stress data.
    :param load_types: Manually override which test result you wish to calibrate for. Default is None which means it
                       will be checked for all available test data ['uniaxial', 'biaxial', 'planar']
    :param incompressible: Does nothing (for now)

    :type mat_data: matdb.classes.MatDataSheet
    """

    def __init__(
        self,
        mat_data,
        num_int=300,
        load_types=None,
        interpolate=True,
        incompressible=True,
    ):
        from ada.core.utils import easy_plotly

        self._mat_data = mat_data
        self._num_int = num_int
        self._load_types = ["uniaxial", "biaxial", "planar"] if load_types is None else load_types
        self._incompressible = incompressible
        self._interpolate = interpolate
        self._params = None
        self._model = None
        self._models_d = dict(Yeoh=dict(model=yeoh, init=[1, 1, 1]), Neo=dict(model=neo_hookean, init=[1]))
        self._selected_models = []
        self._fig = easy_plotly("ADA OpenSim", ([], []), return_widget=True)

    def run(self, model, initial_guess, method="leastsq"):
        """
        Run calibration for a specific model

        :param model: Define which polymer model to calibrate for
        :param initial_guess:
        :param method:
        :return:
        """
        import lmfit

        model_name = model.__name__
        params = [x for x in inspect.getfullargspec(model)[0] if x not in ["strain", "load_type"]]
        params_str = ", ".join([f"{c}" for c in params])
        if len(params) != len(initial_guess):
            raise Exception(
                f'Please include sufficient number of parameters for initial_guess for "{model_name}".\n'
                f"Please input a list containing the required parameters, example: [{params_str}]"
            )

        fit_params = lmfit.Parameters()
        for i, lt in enumerate(self._load_types):
            for param, iguess in zip(params, initial_guess):
                fit_params.add(f"{lt}_{param}", value=iguess)

        if len(self._load_types) > 1:
            for lt in self._load_types[1:]:
                for param in params:
                    fit_params[f"{lt}_{param}"].expr = f"{self._load_types[0]}_{param}"

        # run the global fit to all the data sets
        result = lmfit.minimize(self._calibrate, method=method, params=fit_params, args=(model,))
        self._params = [result.params[x].value for x in result.var_names]
        self._model = model
        self.plot(model, self._params)
        return result

    def _calibrate(self, params, model):
        """

        :param params:
        :param model:
        :return:
        """
        from .utils import S_from_sig, e_from_eps

        eps2e = e_from_eps
        sig2s = S_from_sig
        data = list()
        if "uniaxial" in self._load_types:
            data.append(sig2s(self.uni_eps, self.uni_sig))
        if "biaxial" in self._load_types:
            data.append(sig2s(self.bia_eps, self.bia_sig))
        if "planar" in self._load_types:
            data.append(sig2s(self.pla_eps, self.pla_sig))

        data = np.array(data)

        resid = 0.0 * data[:]

        # Uniaxial
        uni_param = [params[x].value for x in params if "uniaxial" in x]
        if len(uni_param) > 0:
            resid[0, :] = data[0, :] - sig2s(
                eps2e(self.uni_eps),
                model(self.uni_eps, *uni_param, load_type="uniaxial"),
            )

        # Biaxial
        bia_param = [params[x].value for x in params if "biaxial" in x]
        if len(bia_param) > 0:
            resid[1, :] = data[1, :] - sig2s(
                eps2e(self.bia_eps),
                model(self.bia_eps, *bia_param, load_type="biaxial"),
            )

        # Planar
        pla_param = [params[x].value for x in params if "planar" in x]
        if len(pla_param) > 0:
            resid[2, :] = data[2, :] - sig2s(eps2e(self.pla_eps), model(self.pla_eps, *pla_param, load_type="planar"))

        # now flatten this to a 1D array, as minimize() needs
        return resid.flatten()

    def _build_test_data(self, eng_plot=True):
        test_data = self.mat_data.eng_dict if eng_plot else self.mat_data.true_dict
        traces = []
        for key, data in test_data.items():
            traces.append(go.Scatter(name=key, x=data[0], y=data[1]))
        return traces

    def _build_plot(self, model, params, eng_plot=True):
        from .utils import S_from_sig, e_from_eps

        model_name = model.__name__
        eps2e = e_from_eps
        sig2_s = S_from_sig
        traces = list()
        coeff = [x for x in inspect.getfullargspec(model)[0] if x not in ["strain", "load_type"]]
        coeff = "".join([f"{c}:{x:>15.4E}<br>" for c, x in zip(coeff, params)])
        hover_template = "{model_name} coefficients:<br>{coeff}".format(model_name=model_name, coeff=coeff)

        if "uniaxial" in self._load_types:
            uni_fit_sig = model(self.uni_eps, *params, load_type="uniaxial")
            uni_fit_strain = eps2e(self.uni_eps) if eng_plot else self.uni_eps
            uni_fit_stress = sig2_s(eps2e(self.uni_eps), uni_fit_sig) if eng_plot else uni_fit_sig
            traces.append(
                go.Scatter(
                    name=f"{model_name} fit (Uniaxial)",
                    x=uni_fit_strain,
                    y=uni_fit_stress,
                    text=hover_template,
                )
            )
        if "biaxial" in self._load_types:
            bia_fit_sig = model(self.bia_eps, *params, load_type="biaxial")
            bia_fit_strain = eps2e(self.bia_eps) if eng_plot else self.bia_eps
            bia_fit_stress = sig2_s(eps2e(self.bia_eps), bia_fit_sig) if eng_plot else bia_fit_sig
            traces.append(
                go.Scatter(
                    name=f"{model_name} fit (Biaxial)",
                    x=bia_fit_strain,
                    y=bia_fit_stress,
                    text=hover_template,
                )
            )
        if "planar" in self._load_types:
            pla_fit_sig = model(self.pla_eps, *params, load_type="planar")
            pla_fit_strain = eps2e(self.pla_eps) if eng_plot else self.pla_eps
            pla_fit_stress = sig2_s(eps2e(self.pla_eps), pla_fit_sig) if eng_plot else pla_fit_sig
            traces.append(
                go.Scatter(
                    name=f"{model_name} fit (Planar)",
                    x=pla_fit_strain,
                    y=pla_fit_stress,
                    text=hover_template,
                )
            )

        return traces

    def plot(self, model, params, eng_plot=True):
        """

        :param model:
        :param params:
        :param eng_plot: Plot using engineering stress and strain.
        :return:
        """
        model_name = model.__name__
        coeff = [x for x in inspect.getfullargspec(model)[0] if x not in ["strain", "load_type"]]
        name = "".join([f"{c}:{x:>15.4E}<br>" for c, x in zip(coeff, params)])
        annotations = [
            go.layout.Annotation(
                text=f"{model_name} coefficients:<br>{name}",
                align="left",
                showarrow=False,
                xref="paper",
                yref="paper",
                x=0.1,
                y=0.95,
                bordercolor="black",
                borderwidth=1,
            )
        ]
        traces = self._build_test_data(eng_plot)
        traces += self._build_plot(model, params, eng_plot)
        from ada.core.utils import easy_plotly

        return easy_plotly(
            self.mat_data.name + f" ({model_name})",
            ([], []),
            traces=traces,
            mode="lines+markers",
            annotations=annotations,
            xlbl="Engineering Strain",
            ylbl="Engineering Stress [MPa]",
            return_widget=True,
        )

    # region Properties
    @property
    def mat_data(self):
        """

        :return:
        :rtype: matdb.classes.MatDataSheet
        """
        return self._mat_data

    @property
    def uniaxial(self):
        """

        :return:
        :rtype: matdb.classes.TestData
        """
        if self.mat_data is not None:
            if self.mat_data.uniaxial is not None:
                return self.mat_data.uniaxial
        else:
            return None

    @property
    def biaxial(self):
        """

        :return:
        :rtype: matdb.classes.TestData
        """
        from . import MatDataSheet

        if self.mat_data is not None:
            assert isinstance(self.mat_data, MatDataSheet)
            if self.mat_data.biaxial is not None:
                return self.mat_data.biaxial
        else:
            return None

    @property
    def planar(self):
        """

        :return:
        :rtype: matdb.classes.TestData
        """
        from . import MatDataSheet

        if self.mat_data is not None:
            assert isinstance(self.mat_data, MatDataSheet)
            if self.mat_data.planar is not None:
                return self.mat_data.planar
        else:
            return None

    @property
    def uni_eps(self):
        """

        :return: Interpolated or original array of uniaxial strain test data
        """
        if self._interpolate is True:
            return np.linspace(self.uniaxial.eps[0], self.uniaxial.eps[-1], self._num_int)
        else:
            return self.uniaxial.eps

    @property
    def uni_sig(self):
        if self._interpolate is True:
            return np.interp(self.uni_eps, self.uniaxial.eps, self.uniaxial.sig)
        else:
            return self.uniaxial.sig

    @property
    def bia_eps(self):
        """

        :return: Interpolated array of biaxial strain test data
        """
        if self._interpolate is True:
            return np.linspace(self.biaxial.eps[0], self.biaxial.eps[-1], self._num_int)
        else:
            return self.biaxial.eps

    @property
    def bia_sig(self):
        if self._interpolate is True:
            return np.interp(self.bia_eps, self.biaxial.eps, self.biaxial.sig)
        else:
            return self.biaxial.sig

    @property
    def pla_eps(self):
        """

        :return: Interpolated array of planar strain test data
        """
        if self._interpolate is True:
            return np.linspace(self.planar.eps[0], self.planar.eps[-1], self._num_int)
        else:
            return self.planar.eps

    @property
    def pla_sig(self):
        if self._interpolate is True:
            return np.interp(self.pla_eps, self.planar.eps, self.planar.sig)
        else:
            return self.planar.sig

    @property
    def aba_str(self):
        """

        :return: Abaqus input str
        """
        if self._model.__name__ == "yeoh":
            params_str = ", ".join([str(x) for x in self._params])
            return f"""*Material, name=yeoh_mat
*Hyperelastic, yeoh
 {params_str},          0.,          0.,          0."""
        elif self._model.__name__ == "neo_hookean":
            return f"""*Material, name=neo_hookean_mat
*Hyperelastic, neo hooke
 {self._params[0]},0."""
        else:
            print(f'unrecognized model "{self._model.__name__}"')
            return ""

    # endregion

    def _clicked_models(self, p):
        self._selected_models = p["new"]

    def _click_plot(self, p):
        self._fig.data = []
        self._fig.add_traces(self._build_test_data())
        model_name = self._selected_models
        model_names = ""
        for m in model_name:
            model = self._models_d[m]["model"]
            model_names += " " + m
            param = self._models_d[m]["init"]
            self.run(model, param)

            traces = self._build_plot(self._model, self._params)
            self._fig.add_traces(traces)

        self._fig.layout["title"] = "ADA OpenSim: " + self._model.__name__

    def _repr_html_(self):
        from ada.core.utils import easy_plotly

        self._fig = easy_plotly("ADA OpenSim", ([], []), return_widget=True)
        opt_models = list(self._models_d.keys())
        self._selected_models = ["Yeoh"]
        opts_odb = widgets.SelectMultiple(options=opt_models, value=["Yeoh"], disabled=False)
        center = widgets.Button(
            description="plot",
            button_style="",  # 'success', 'info', 'warning', 'danger' or ''
        )
        opts_odb.observe(self._clicked_models, "value")
        center.on_click(self._click_plot)
        # self._clicked_models({'new': 'Yeoh'})
        display(widgets.VBox([widgets.HBox([widgets.VBox([opts_odb, center])]), self._fig]))


# region Incompressible
def neo_hookean(strain, mu, load_type="uniaxial"):
    """
    Neo-Hookean incompressible uniaxial

    :param strain:
    :param mu: Shear modulus
    :param load_type: Type of loading
    :return:
    """
    lam = np.exp(strain)
    if load_type == "uniaxial":
        return mu * (lam * lam - 1.0 / lam)
    elif load_type == "biaxial":
        return mu * (lam * lam - 1.0 / lam ** 4)
    elif load_type == "planar":
        return mu * (lam * lam - 1.0 / lam ** 2)
    else:
        print(f"unknown load type {load_type}")
        return None


def yeoh(strain, c10, c20, c30, load_type="uniaxial"):
    """
    Yeoh incompressible

    :param strain:
    :param c10:
    :param c20:
    :param c30:
    :param load_type:
    :return:
    """
    lam = np.exp(strain)
    i1 = lam ** 2 + 2.0 / lam
    if load_type == "uniaxial":
        return 2 * (c10 + 2 * c20 * (i1 - 3) + 3 * c30 * (i1 - 3) ** 2) * (lam ** 2 - 1.0 / lam)
    elif load_type == "biaxial":
        return 2 * (c10 + 2 * c20 * (i1 - 3) + 3 * c30 * (i1 - 3) ** 2) * (lam ** 2 - 1.0 / lam ** 4)
    elif load_type == "planar":
        return 2 * (c10 + 2 * c20 * (i1 - 3) + 3 * c30 * (i1 - 3) ** 2) * (lam ** 2 - 1.0 / lam ** 2)
    else:
        print(f"unknown load type {load_type}")
        return None


# endregion

# region Compressible
def NH_3D(stretch, param):
    """
    Neo-Hookean. 3D loading specified by stretches.

    param[0]=mu, param[1]=kappa

    :param stretch:
    :param param:
    :return:
    """
    mu, kappa = param
    F = np.array([[stretch[0], 0, 0], [0, stretch[1], 0], [0, 0, stretch[2]]], dtype="float")
    J = np.linalg.det(F)
    Fstar = J ** (-1 / 3) * F
    bstar = np.dot(Fstar, Fstar.T)
    dev_bstar = bstar - np.trace(bstar) / 3 * np.eye(3)
    return mu / J * dev_bstar + kappa * (J - 1) * np.eye(3)


def Yeoh_3D(stretch, param):
    """Yeoh. 3D loading specified by stretches.
    param: [C10, C20, C30, kappa]. Returns true stress."""
    L1 = stretch[0]
    L2 = stretch[1]
    L3 = stretch[2]
    F = np.array([[L1, 0, 0], [0, L2, 0], [0, 0, L3]], dtype="float")
    J = np.linalg.det(F)
    bstar = J ** (-2.0 / 3.0) * np.dot(F, F.T)
    devbstar = bstar - np.trace(bstar) / 3 * np.eye(3)
    I1s = np.trace(bstar)
    return 2 / J * (param[0] + 2 * param[1] * (I1s - 3) + 3 * param[2] * (I1s - 3) ** 2) * devbstar + param[3] * (
        J - 1
    ) * np.eye(3)


def uniaxial_stress(model, true_strain, params):
    """
    Compressible uniaxial loading. Returns true stress.

    :param model:
    :param true_strain: numpy array
    :param params:
    :return:
    """

    stress = np.zeros(len(true_strain))
    for i in range(len(true_strain)):
        lam1 = np.exp(true_strain[i])

        def calc_s22_abs(x):
            return abs(model([lam1, x, x], params)[1, 1])

        # calc_s22_abs = lambda x: abs(model([lam1, x, x], params)[1, 1])
        # search for transverse stretch that gives S22=0
        x0 = 1.0 / np.sqrt(lam1)
        lam2 = scipy.optimize.fmin(calc_s22_abs, x0=x0, xtol=1e-9, ftol=1e-9, disp=False)
        stress[i] = model([lam1, lam2, lam2], params)[0, 0]
    return stress


def biaxial_stress(model, trueStrainVec, params):
    """
    Compressible biaxial loading. Returns true stress.

    :param model:
    :param trueStrainVec:
    :param params:
    :return:
    """

    stress = np.zeros(len(trueStrainVec))
    for i in range(len(trueStrainVec)):
        lam1 = np.exp(trueStrainVec[i])

        def calc_s33_abs(x):
            abs(model([lam1, lam1, x], params)[2, 2])

        # search for transverse stretch that gives S33=0
        lam3 = scipy.optimize.fmin(calc_s33_abs, x0=1 / np.sqrt(lam1), xtol=1e-9, ftol=1e-9, disp=False)

        stress[i] = model([lam1, lam1, lam3], params)[0, 0]
    return stress


def planar_stress(model, trueStrainVec, params):
    """
    Compressible planar loading. Returns true stress.

    :param model:
    :param trueStrainVec:
    :param params:
    :return:
    """
    stress = np.zeros(len(trueStrainVec))
    for i in range(len(trueStrainVec)):
        lam1 = np.exp(trueStrainVec[i])

        def calc_s33_abs(x):
            abs(model([lam1, 1.0, x], params)[2, 2])

        # search for transverse stretch that gives S33=0
        lam3 = scipy.optimize.fmin(calc_s33_abs, x0=1 / np.sqrt(lam1), xtol=1e-9, ftol=1e-9, disp=False)
        stress[i] = model([lam1, 1.0, lam3], params)[0, 0]
    return stress


# endregion
