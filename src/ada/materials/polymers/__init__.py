import numpy as np


class MatDataSheet:
    """

    :param name:
    :param mat_class:
    :param mat_type:
    :param testdata:
    """

    def __init__(self, name, mat_class, mat_type, testdata):
        self._name = name
        self._class = mat_class
        self._type = mat_type
        self._test_data = testdata

    @property
    def name(self):
        return self._name

    @property
    def mat_class(self):
        return self._class

    @property
    def type(self):
        return self._type

    @property
    def test_data(self):
        return self._test_data

    @property
    def uniaxial(self):
        for td in self.test_data:
            if "uniaxial" in td.load_type.lower():
                return td
        return None

    @property
    def biaxial(self):
        for td in self.test_data:
            if "biaxial" in td.load_type.lower():
                return td
        return None

    @property
    def planar(self):
        for td in self.test_data:
            if "planar" in td.load_type.lower():
                return td
        return None

    @property
    def visualize_eng(self):
        from ada.core.utils import easy_plotly

        in_data = {}
        for d in self.test_data:
            in_data[f"{d.name} ({d.load_type})"] = (d.e, d.S)
        return easy_plotly(
            self.name,
            in_data,
            mode="lines+markers",
            xlbl="Engineering Strain",
            ylbl="Engineering Stress [MPa]",
        )

    @property
    def visualize_true(self):
        in_data = dict()
        from ada.core.utils import easy_plotly

        for test in self.test_data:
            in_data[test.title] = (test.eps, test.sig)
        return easy_plotly(
            self.name,
            in_data,
            mode="lines+markers",
            xlbl="True Strain",
            ylbl="True Stress [MPa]",
        )

    @property
    def true_dict(self):
        in_data = dict()
        for d in self.test_data:
            in_data[f"{d.name} ({d.load_type})"] = (d.eps, d.sig)
        return in_data

    @property
    def eng_dict(self):
        in_data = dict()
        for d in self.test_data:
            in_data[f"{d.name} ({d.load_type})"] = (d.e, d.S)
        return in_data

    def __repr__(self):
        tests = "\n".join([str(x) for x in self.test_data])
        return f'{self.name}\nClass: "{self.mat_class}", Type: "{self.type}"\nTest Data:\n{tests}'


class TestData:
    def __init__(self, mat_data_path, incompressible=True):
        self._name = None
        self._standard = None
        self._temperature = None
        self._load_type = None
        self._source = None
        self._created_by = None
        self._incompressible = incompressible
        with open(mat_data_path, "r") as d:
            lines = d.readlines()
        comments = ""
        data = []
        cols = []
        for li in lines:
            if "#" in li[0]:
                if "created by" in li.lower():
                    self._created_by = li.split(":")[-1].strip()
                if "column" in li:
                    cols.append(li.split(":")[-1].strip())
                if "standard" in li.lower():
                    self._standard = li.split(":")[-1].strip() if "none" not in li.lower() else None
                if "temperature" in li.lower():
                    self._temperature = li.split(":")[-1].strip()
                if "load type" in li.lower():
                    self._load_type = li.split(":")[-1].strip()
                if "source" in li.lower():
                    self._source = li.split(":")[-1].strip()
                if "name" in li.lower():
                    self._name = li.split(":")[-1].strip()
                comments += "".join(li[1:])

            else:
                d = li.split(",")
                data.append([float(x) for x in d])

        self._data = list(zip(*data))
        self._cols = cols
        self._comments = comments

    @property
    def standard(self):
        return self._standard

    @property
    def load_type(self):
        return self._load_type

    @property
    def comments(self):
        return self._comments

    @property
    def cols(self):
        return self._cols

    @property
    def data(self):
        return self._data

    @property
    def eps(self):
        """

        :return: True strain
        """
        for i, c in enumerate(self.cols):
            if "true strain" in c.lower():
                return np.array([self.data[i]])

        if self.e is not None:
            return np.array([np.log(1 + e) for e in self.e])

        return None

    @property
    def sig(self):
        """

        :return: True stress
        """
        for i, c in enumerate(self.cols):
            if "true stress" in c.lower():
                return np.array(self.data[i])

        if self.S is not None:
            return np.array([s * (1 + eps) for eps, s in zip(self.eps, self.S)])

        return None

    @property
    def e(self):
        """

        :return: Engineering strain
        """
        for i, c in enumerate(self.cols):
            if "engineering strain" in c.lower():
                return np.array(self.data[i])

        if self.eps is not None:
            if self._incompressible is True:
                return np.exp(self.eps) - 1

        return None

    @property
    def S(self):
        """

        :return: Engineering stress
        """
        for i, c in enumerate(self.cols):
            if "engineering stress" in c.lower():
                return np.array(self.data[i])

        if self.sig is not None:
            return np.array([sig / (1 + eps) for eps, sig in (self.eps, self.sig)])
        return None

    @property
    def name(self):
        return self._name

    @property
    def title(self):
        if self.standard is not None:
            return self.standard + f" ({self.load_type}) Engineering and True stress-strain curves"
        else:
            return self._name + f" ({self.load_type}) Engineering and True stress-strain curves"

    @property
    def visualize(self):
        from ada.core.utils import easy_plotly

        in_data = {"Engineering": (self.e, self.S), "True": (self.eps, self.sig)}
        return easy_plotly(self.title, in_data, xlbl="Strain [-]", ylbl="Stress [Pa]")

    def __repr__(self):
        cols = ", ".join([f'"{e}"' for e in self.cols])
        return f"{self.standard}: {self.load_type} (columns: {cols})"
