from ada.config import Settings


class FemBase:
    def __init__(self, name, metadata, parent):
        self.name = name
        self.parent = parent
        self._metadata = metadata if metadata is not None else dict()

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        from ada.core.utils import make_name_fem_ready

        if str.isnumeric(value[0]):
            raise ValueError("Name cannot start with numeric")

        if Settings.convert_bad_names_for_fem:
            self._name = make_name_fem_ready(value)
        else:
            self._name = value.strip()

    @property
    def parent(self):
        """

        :rtype: ada.fem.FEM
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        # if type(value) not in (FEM, Step):
        #     raise ValueError(f'Parent type "{type(value)}" is not supported')
        self._parent = value

    @property
    def metadata(self):
        return self._metadata

    @property
    def on_assembly_level(self):
        """

        :return:
        """
        # TODO: This is not really working correctly. This must be fixed
        from ada import Assembly

        return True if type(self.parent.parent) is Assembly else False

    @property
    def instance_name(self):
        if self.on_assembly_level is False:
            return self.name
        else:
            return self.parent.instance_name + "." + self.name


class Csys(FemBase):
    _valid_systems = ["RECTANGULAR"]  # , 'CYLINDRICAL', 'SPHERICAL', 'Z RECTANGULAR', 'USER']
    _valid_defs = ["COORDINATES", "NODES"]  # ,'OFFSET TO NODES'

    def __init__(
        self,
        name,
        definition="COORDINATES",
        system="RECTANGULAR",
        nodes=None,
        coords=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._definition = definition
        self._system = system
        self._nodes = nodes
        self._coords = coords

    @property
    def definition(self):
        return self._definition

    @property
    def system(self):
        return self._system

    @property
    def nodes(self):
        return self._nodes

    @property
    def coords(self):
        return self._coords

    def __repr__(self):
        content_map = dict(COORDINATES=self.coords, NODES=self.nodes)
        return f'Csys("{self.name}", "{self.definition}", {content_map[self.definition]})'


class Amplitude(FemBase):
    def __init__(self, name, x, y, smooth=None, metadata=None, parent=None):
        super().__init__(name, metadata, parent)
        self._x = x
        self._y = y
        self._smooth = smooth

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def smooth(self):
        return self._smooth
