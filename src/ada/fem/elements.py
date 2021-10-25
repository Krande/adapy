from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Union

import numpy as np

from ada.concepts.piping import Pipe
from ada.concepts.points import Node
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate, Wall

from .common import Csys, FemBase
from .sections import ConnectorSection, FemSection
from .shapes import ElemShape, ElemType


class Elem(FemBase):

    EL_TYPES = ElemType

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
        """:type fem_sec: ada.fem.FemSection"""
        super(Elem, self).__init__(el_id, metadata, parent)
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
        self._hinge_prop = None
        self._eccentricity = None
        self._refs = []

    def get_offset_coords(self):
        nodes = [n.p for n in self.nodes]
        if self.eccentricity is None:
            return nodes

        mat = np.eye(3)

        e1 = self.eccentricity.end1
        e2 = self.eccentricity.end2

        if e1 is not None:
            ecc1 = e1.ecc_vector
            n_old = e1.node
            nodes[0] = np.dot(mat, ecc1) + n_old.p

        if e2 is not None:
            ecc2 = e2.ecc_vector
            n_old = e2.node
            nodes[-1] = np.dot(mat, ecc2) + n_old.p

        return nodes

    @property
    def type(self):
        return self._el_type

    @type.setter
    def type(self, value):
        from .shapes import ElemShape

        if ElemShape.is_valid_elem(value) is False:
            raise ValueError(f'Currently unsupported element type "{value}".')
        self._el_type = value.upper()

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def id(self) -> int:
        return self._el_id

    @id.setter
    def id(self, value):
        if type(value) not in (np.int32, int, np.uint64) and issubclass(type(self), Connector) is False:
            raise ValueError(f'Element ID "{type(value)}" must be numeric')
        self._el_id = value

    @property
    def nodes(self) -> List[Node]:
        return self._nodes

    @property
    def hinge_prop(self) -> Union[None, HingeProp]:
        return self._hinge_prop

    @hinge_prop.setter
    def hinge_prop(self, value: HingeProp):
        self._hinge_prop = value

    @property
    def eccentricity(self) -> Union[None, Eccentricity]:
        return self._eccentricity

    @eccentricity.setter
    def eccentricity(self, value: Eccentricity):
        self._eccentricity = value

    @property
    def elset(self):
        return self._elset

    @property
    def fem_sec(self) -> FemSection:
        return self._fem_sec

    @fem_sec.setter
    def fem_sec(self, value):
        self._fem_sec = value

    @property
    def mass_props(self) -> Mass:
        return self._mass_props

    @mass_props.setter
    def mass_props(self, value):
        self._mass_props = value

    @property
    def shape(self) -> ElemShape:
        if self._shape is None:
            self._shape = ElemShape(self.type, self.nodes)
        return self._shape

    @property
    def refs(self) -> List[Union[Elem, Beam, Plate, Pipe, Wall, Shape]]:
        return self._refs

    def update(self):
        self._nodes = list(set(self.nodes))
        if len(self.nodes) <= 1:
            self._el_id = None
        else:
            self._shape = None

    def __repr__(self):
        return f'Elem(ID: {self._el_id}, Type: {self.type}, NodeIds: "{self.nodes}")'


@dataclass
class Hinge:
    retained_dofs: List[int]
    csys: Csys
    concept_node: Node = None
    fem_node: Node = None
    elem_n_index: int = None
    beam_n_index: int = None
    constraint_ref = None


@dataclass
class HingeProp:
    end1: Hinge = None
    end2: Hinge = None
    elem_ref: Elem = None
    beam_ref: Beam = None


@dataclass
class EccPoint:
    node: Node
    ecc_vector: np.ndarray


@dataclass
class Eccentricity:
    end1: EccPoint = None
    end2: EccPoint = None
    sh_ecc_vector: np.ndarray = None


class Connector(Elem):
    def __init__(
        self,
        name,
        el_id,
        n1: Node,
        n2: Node,
        con_type,
        con_sec: ConnectorSection,
        preload=None,
        csys: Csys = None,
        metadata=None,
        parent=None,
    ):
        """:type parent: ada.FEM"""
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
    def con_sec(self) -> ConnectorSection:
        return self._con_sec

    @property
    def n1(self) -> Node:
        return self._n1

    @property
    def n2(self) -> Node:
        return self._n2

    @property
    def csys(self) -> Csys:
        return self._csys

    def __repr__(self):
        return f'ConnectorElem(ID: {self.id}, Type: {self.type}, End1: "{self.n1}", End2: "{self.n2}")'


class Spring(Elem):
    def __init__(self, name, el_id, el_type, stiff, fem_set, metadata=None, parent=None):
        """:type fem_set: ada.fem.FemSet"""

        super(Spring, self).__init__(el_id, fem_set.members, el_type)
        super(Elem, self).__init__(name, metadata, parent)
        self._stiff = stiff
        self._n1 = fem_set.members[0]
        self._n2 = None
        if len(fem_set.members) > 1:
            self._n2 = fem_set.members[-1]
        self._fem_set = fem_set

    @property
    def fem_set(self):
        """:rtype: ada.fem.sets.FemSet"""
        return self._fem_set

    @property
    def stiff(self):
        return self._stiff

    def __repr__(self):
        return f'Spring("{self.name}", type="{self._stiff}")'


class MassTypes:
    MASS = "MASS"
    NONSTRU = "NONSTRUCTURAL MASS"
    ROT_INERTIA = "ROTARY INERTIA"

    all = [MASS, NONSTRU, ROT_INERTIA]


class MassPType:
    ISOTROPIC = "ISOTROPIC"
    ANISOTROPIC = "ANISOTROPIC"

    all = [ISOTROPIC, ANISOTROPIC]


class Mass(FemBase):
    TYPES = MassTypes
    PTYPES = MassPType

    def __init__(
        self,
        name,
        fem_set,
        mass,
        mass_type=None,
        ptype=None,
        mass_id: int = None,
        units=None,
        metadata=None,
        parent=None,
    ):
        """:type fem_set: ada.fem.FemSet"""
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        if mass is None:
            raise ValueError("Mass cannot be None")
        if type(mass) not in (list, tuple):
            logging.info(f"Mass {type(mass)} converted to list of len=1. Assume equal mass in all 3 transl. DOFs.")
            ptype = self.PTYPES.ISOTROPIC
            mass = [mass]
        self._mass = mass
        self._mass_type = mass_type.upper() if mass_type is not None else self.TYPES.MASS
        if self.type not in MassTypes.all:
            raise ValueError(f'Mass type "{self.type}" is not in list of supported types {MassTypes.all}')
        if ptype not in MassPType.all and ptype is not None:
            raise ValueError(f'Mass point type "{ptype}" is not in list of supported types {MassPType.all}')
        self.point_mass_type = ptype
        self._units = units
        self._id = mass_id
        self._check_input()

    def _check_input(self):
        if self.point_mass_type is None:
            if self.type == MassTypes.MASS:
                if type(self._mass) in (list, tuple):
                    raise ValueError("Mass can only be a scalar number for Isotropic mass")
        elif self.point_mass_type == MassPType.ISOTROPIC:
            if (len(self._mass) == 1) is False:
                raise ValueError("Mass can only be a scalar number for Isotropic mass")
        elif self.point_mass_type == MassPType.ANISOTROPIC:
            if (len(self._mass) == 3) is False:
                raise ValueError("Mass must be specified for 3 dofs for Anisotropic mass")
        else:
            raise ValueError(f'Unknown mass input "{self.type}"')

    @property
    def id(self):
        return self._id

    @property
    def type(self):
        return self._mass_type

    @property
    def fem_set(self):
        """:rtype: ada.fem.FemSet"""
        return self._fem_set

    @fem_set.setter
    def fem_set(self, value: Mass):
        self._fem_set = value

    @property
    def mass(self):
        if self.point_mass_type is None:
            if self.type == MassTypes.MASS:
                if type(self._mass) in (list, tuple):
                    raise ValueError("Mass can only be a scalar number for Isotropic mass")
                return float(self._mass[0])
            elif self.type == MassTypes.NONSTRU:
                return self._mass
            else:
                return float(self._mass)
        elif self.point_mass_type == MassPType.ISOTROPIC:
            if (len(self._mass) == 1) is False:
                raise ValueError("Mass can only be a scalar number for Isotropic mass")
            return self._mass[0]
        elif self.point_mass_type == MassPType.ANISOTROPIC:
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
