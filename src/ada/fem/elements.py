import logging

import numpy as np

from ada.materials import Material

from .common import Csys, FemBase
from .sets import FemSet


class Elem(FemBase):
    """
    Node numbering of elements is based on GMSH doc here http://gmsh.info/doc/texinfo/gmsh.html#Node-ordering

    :param el_id:
    :param nodes:
    :param el_type:
    :param elset:
    :param fem_sec:
    :param mass_props:
    :param parent:
    :param metadata:
    :type fem_sec: FemSection
    """

    def __init__(
        self,
        el_id,
        nodes,
        el_type,
        elset=None,
        fem_sec=None,
        mass_props=None,
        parent=None,
        metadata=None,
    ):
        from ada import Node

        super().__init__(el_id, metadata, parent)
        self.type = el_type.upper()
        self._el_id = el_id

        self._shape = None

        if type(nodes[0]) is Node:
            for node in nodes:
                node.refs.append(self)

        self._nodes = nodes
        self._elset = elset
        self._fem_sec = fem_sec
        self._mass_props = mass_props
        self._refs = []

    @property
    def type(self):
        """

        :return: Element type.
        """
        return self._el_type

    @type.setter
    def type(self, value):
        from .shapes import ElemShapes

        if ElemShapes.is_valid_elem(value) is False:
            raise ValueError(f'Currently unsupported element type "{value}".')
        self._el_type = value.upper()

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def id(self):
        return self._el_id

    @id.setter
    def id(self, value):
        if type(value) not in (np.int32, int, np.uint64) and issubclass(type(self), Connector) is False:
            raise ValueError(f'Element ID "{type(value)}" must be numeric')
        self._el_id = value

    @property
    def nodes(self):
        return self._nodes

    @property
    def elset(self):
        return self._elset

    @property
    def fem_sec(self):
        """

        :return:
        :rtype: ada.fem.FemSection
        """
        return self._fem_sec

    @fem_sec.setter
    def fem_sec(self, value):
        self._fem_sec = value

    @property
    def mass_props(self):
        """

        :return:
        :rtype: ada.fem.Mass
        """
        return self._mass_props

    @mass_props.setter
    def mass_props(self, value):
        self._mass_props = value

    @property
    def shape(self):
        """

        :return:
        :rtype: ada.fem.ElemShapes
        """
        from .shapes import ElemShapes

        if self._shape is None:
            self._shape = ElemShapes(self.type, self.nodes)
        return self._shape

    @property
    def refs(self):
        return self._refs

    def update(self):
        from toolz import unique

        self._nodes = list(unique(self.nodes))
        if len(self.nodes) <= 1:
            self._el_id = None
        else:
            self._shape = None

    def __repr__(self):
        return f'Elem(ID: {self._el_id}, Type: {self.type}, NodeIds: "{self.nodes}")'


class FemSection(FemBase):
    def __init__(
        self,
        name,
        sec_type,
        elset: FemSet,
        material: Material,
        section=None,
        local_z=None,
        local_y=None,
        thickness=None,
        int_points=5,
        offset=None,
        hinges=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._sec_type = sec_type
        if sec_type is None:
            raise ValueError("Section type cannot be None")
        self._elset = elset
        self._material = material
        self._section = section
        self._local_z = local_z
        self._local_y = local_y
        self._thickness = thickness
        self._int_points = int_points
        self._offset = offset
        self._hinges = hinges

    def link_elements(self):
        def link_elem(el):
            """

            :param el:
            :type el: Elem
            """
            el.fem_sec = self

        list(map(link_elem, self.elset.members))

    def get_offset_coords(self):
        """

        :param self:
        :type self: FemSection
        :return:
        """

        elem = self.elset.members[0]
        nodes = [n.p for n in elem.nodes]
        if self.offset is None:
            return nodes

        for n_old, ecc in self.offset:
            mat = np.eye(3)
            new_p = np.dot(mat, ecc) + n_old.p
            i = elem.nodes.index(n_old)
            nodes[i] = new_p

        return nodes

    @property
    def type(self):
        return self._sec_type

    @property
    def elset(self):
        return self._elset

    @elset.setter
    def elset(self, value):
        self._elset = value

    @property
    def local_z(self):
        """

        :return: Local Z describes the up vector of the cross section
        :rtype: list
        """
        from ada.core.utils import roundoff, unit_vector, vector_length

        if self._local_z is None:
            if self.type == "beam":
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    logging.error(f"Element {self.elset.members[0].id} has zero length")
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)

                crossed = np.cross(xvec, self.local_y)
                ma = max(abs(crossed))
                self._local_z = tuple([roundoff(x / ma, 3) for x in crossed])
            else:
                from ada.core.utils import normal_to_points_in_plane

                self._local_z = normal_to_points_in_plane([n.p for n in self.elset.members[0].nodes])
                # raise NotImplementedError("Local Z is not implemented for shell elements, yet.")
        return self._local_z

    @property
    def local_y(self):
        """



        :return: Local Z describes the up vector of the cross section
        :rtype: list
        """
        from ada.core.utils import roundoff, unit_vector, vector_length

        if self._local_y is None:
            if self.type in ("beam", "shell"):
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)
                # See https://en.wikipedia.org/wiki/Cross_product#Coordinate_notation for order of cross product
                crossed = np.cross(self.local_z, xvec)
                ma = max(abs(crossed))
                self._local_y = tuple([roundoff(x / ma, 3) for x in crossed])
            else:
                raise NotImplementedError("Local Y is not implemented for solid elements.")
        return self._local_y

    @property
    def local_x(self):
        if self.type == "beam":
            from ada.core.utils import unit_vector

            el = self.elset.members[0]
            return unit_vector(el.nodes[-1].p - el.nodes[0].p)
        else:
            logging.error(f"X-vector not defined for {self.type}")

    @property
    def csys(self):
        return [self.local_x, self.local_y, self.local_z]

    @property
    def section(self):
        """

        :return:
        :rtype: ada.Section
        """
        return self._section

    @property
    def material(self):
        """

        :return:
        :rtype: ada.Material
        """
        return self._material

    @property
    def thickness(self):
        return self._thickness

    @property
    def int_points(self):
        return self._int_points

    @property
    def offset(self):
        return self._offset

    @property
    def hinges(self):
        return self._hinges

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key:
                continue
            # Re-evaluate the elset exemption. Maybe this should raise False based on the fem set only?
            # if 'elset' in key:
            #     for m in self.__dict__[key].members:
            #         if m.type != other.__dict__[key].members[0].type:
            #             return False
            if other.__dict__[key] != val:
                return False

        return True

    def __repr__(self):
        return (
            f'FemSection({self.type} - name: "{self.name}", sec: "{self.section}", '
            f'mat: "{self.material}",  elset: "{self.elset}")'
        )


class Connector(Elem):
    """
    A Connector Element

    :param name:
    :param con_type:
    :param con_sec:
    :param el_id:
    :param n1:
    :param n2:
    :param csys:

    :type n1: ada.Node
    :type n2: ada.Node
    :type con_sec: ConnectorSection
    :type csys: Csys
    """

    def __init__(
        self,
        name,
        el_id,
        n1,
        n2,
        con_type,
        con_sec,
        preload=None,
        csys=None,
        metadata=None,
        parent=None,
    ):
        from ada import Node

        if type(n1) is not Node or type(n2) is not Node:
            raise ValueError("Connector Start\\end must be nodes")
        super(Connector, self).__init__(el_id, [n1, n2], "CONNECTOR")
        super(Elem, self).__init__(name, metadata, parent)
        self._n1 = n1
        self._n2 = n2
        self._con_type = con_type
        self._con_sec = con_sec
        self._preload = preload
        self._csys = csys if csys is not None else Csys(f"{name}_csys")

    @property
    def con_type(self):
        return self._con_type

    @property
    def con_sec(self):
        """

        :return:
        :rtype: ConnectorSection
        """
        return self._con_sec

    @property
    def n1(self):
        """

        :return:
        :rtype: ada.Node
        """
        return self._n1

    @property
    def n2(self):
        """

        :return:
        :rtype: ada.Node
        """
        return self._n2

    @property
    def csys(self):
        """

        :return:
        :rtype: Csys
        """
        return self._csys

    def __repr__(self):
        return f'ConnectorElem(ID: {self.id}, Type: {self.type}, End1: "{self.n1}", End2: "{self.n2}")'


class ConnectorSection(FemBase):
    """
    A Connector Section


    :param name:
    :param elastic_comp:
    :param damping_comp:
    :param plastic_comp:
    :param rigid_dofs:
    :param soft_elastic_dofs:
    """

    def __init__(
        self,
        name,
        elastic_comp,
        damping_comp,
        plastic_comp=None,
        rigid_dofs=None,
        soft_elastic_dofs=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._elastic_comp = elastic_comp if elastic_comp is not None else []
        self._damping_comp = damping_comp if damping_comp is not None else []
        self._plastic_comp = plastic_comp
        self._rigid_dofs = rigid_dofs
        self._soft_elastic_dofs = soft_elastic_dofs

    @property
    def elastic_comp(self):
        return self._elastic_comp

    @property
    def damping_comp(self):
        return self._damping_comp

    @property
    def plastic_comp(self):
        return self._plastic_comp

    @property
    def rigid_dofs(self):
        return self._rigid_dofs

    @property
    def soft_elastic_dofs(self):
        return self._soft_elastic_dofs


class Spring(Elem):
    """

    :param name:
    :param el_id:
    :param el_type:
    :param stiff:
    :param n1:
    :param n2:
    :param metadata:
    :param parent:
    """

    def __init__(self, name, el_id, el_type, stiff, n1, n2=None, metadata=None, parent=None):
        nids = [n1]
        if n2 is not None:
            nids += [n2]
        super(Spring, self).__init__(el_id, nids, el_type)
        super(Elem, self).__init__(name, metadata, parent)
        self._stiff = stiff
        self._n1 = n1
        self._n2 = n2
        self._fem_set = FemSet(self.name + "_set", [el_id], "elset")
        if self.parent is not None:
            self.parent.sets.add(self._fem_set)

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def stiff(self):
        return self._stiff

    def __repr__(self):
        return f'Spring("{self.name}", type="{self._stiff}")'


class Mass(FemBase):
    """

    :param name: Name of set
    :param fem_set: Fem Set (of element or nodal type)
    :type fem_set: FemSet
    :param mass: Mass magnitude
    :param mass_type: Type of mass. See _valid_types. Default is 'MASS'
    :param ptype: Point mass type. Can be None, 'Isotropic' or 'Anisotropic'
    :param units:
    :param metadata:
    :param parent:

    """

    _valid_types = ["MASS", "NONSTRUCTURAL MASS", "ROTARY INERTIA"]
    _valid_ptypes = [None, "ISOTROPIC", "ANISOTROPIC"]

    def __init__(
        self,
        name,
        fem_set,
        mass,
        mass_type=None,
        ptype=None,
        units=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        if mass is None:
            raise ValueError("Mass cannot be None")
        if type(mass) not in (list, tuple):
            logging.info(f"Mass {type(mass)} converted to list of len=1. Assume equal mass in all 3 transl. DOFs.")
            mass = [mass]
        self._mass = mass
        self._mass_type = mass_type if mass_type is not None else "MASS"
        if self.type not in Mass._valid_types:
            raise ValueError(f'Mass type "{self.type}" is not in list of supported types {self._valid_types}')
        if ptype not in self._valid_ptypes:
            raise ValueError(f'Mass point type "{ptype}" is not in list of supported types {self._valid_ptypes}')
        self.point_mass_type = ptype
        self._units = units

    @property
    def type(self):
        return self._mass_type.upper()

    @property
    def fem_set(self):
        """
        :rtype: FemSet
        """
        return self._fem_set

    @property
    def mass(self):
        if self.point_mass_type is None:
            if self.type == "MASS":
                if type(self._mass) in (list, tuple):
                    raise ValueError("Mass can only be a scalar number for Isotropic mass")
                return float(self._mass[0])
            elif self.type == "NONSTRUCTURAL MASS":
                return self._mass
            else:
                return float(self._mass)
        elif self.point_mass_type == "ISOTROPIC":
            if (len(self._mass) == 1) is False:
                raise ValueError("Mass can only be a scalar number for Isotropic mass")
            return self._mass[0]
        elif self.point_mass_type == "ANISOTROPIC":
            if (len(self._mass) == 3) is False:
                raise ValueError("Mass must be specified for 3 dofs for Anisotropic mass")
            return self._mass
        else:
            raise ValueError(f'Unknown mass input "{self.type}"')

    @property
    def units(self):
        return self._units

    @property
    def point_mass_type(self):
        return self._ptype

    @point_mass_type.setter
    def point_mass_type(self, value):
        self._ptype = value

    def __repr__(self):
        return f"Mass({self.name}, {self.point_mass_type}, [{self.mass}])"
