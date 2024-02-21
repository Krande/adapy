from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.api.nodes import Node
from ada.config import logger

from .common import Csys, FemBase
from .shapes import ElemShape, ElemType
from .shapes import definitions as shape_def
from .shapes.definitions import LineShapes, ShapeResolver, ShellShapes, SolidShapes

if TYPE_CHECKING:
    from ada import FEM, Beam, Pipe, Plate, Shape, Wall
    from ada.fem import ConnectorSection, FemSection, FemSet


class Elem(FemBase):
    EL_TYPES = ElemType

    def __init__(
        self,
        el_id,
        nodes: list[Node],
        el_type: str | ShellShapes | LineShapes | SolidShapes,
        elset=None,
        fem_sec: FemSection = None,
        mass_props=None,
        parent: FEM = None,
        el_formulation_override=None,
        metadata=None,
    ):
        super(Elem, self).__init__(el_id, metadata, parent)
        self.type = el_type
        self._el_id = el_id
        self._shape = None

        if nodes is not None and isinstance(nodes[0], Node):
            for node in nodes:
                node.add_obj_to_refs(self)

        self._nodes = nodes
        self._elset = elset
        self._fem_sec = fem_sec
        self._mass_props = mass_props
        self._hinge_prop = None
        self._eccentricity = None
        self._formulation_override = el_formulation_override
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

    def replace_node_with_other_node(self, old_node: Node, new_node: Node):
        index = None
        for i, node in enumerate(self.nodes):
            if node == old_node:
                index = i
        if index is None:
            raise ValueError(f'Unable to find {old_node.id} in this element "{self.id}"')
        self.nodes.pop(index)
        self.nodes.insert(index, new_node)

    @property
    def type(self) -> ShellShapes | LineShapes | SolidShapes:
        return self._el_type

    @type.setter
    def type(self, value: str | ShellShapes | LineShapes | SolidShapes):
        if isinstance(value, str):
            result = ShapeResolver.get_el_type_from_str(value)
            if result is None:
                raise ValueError(f'Currently unsupported element type "{value}".')
        else:
            result = value

        self._el_type = result

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
        if type(value) not in (np.int32, int, np.uint64, np.int64) and issubclass(type(self), Connector) is False:
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
    def elset(self) -> FemSet:
        return self._elset

    @elset.setter
    def elset(self, value: FemSet):
        self._elset = value

    @property
    def fem_sec(self) -> FemSection:
        return self._fem_sec

    @fem_sec.setter
    def fem_sec(self, value: FemSection):
        self._fem_sec = value

    @property
    def mass_props(self) -> Mass:
        return self._mass_props

    @mass_props.setter
    def mass_props(self, value: Mass):
        self._mass_props = value

    @property
    def shape(self) -> ElemShape:
        if self._shape is None:
            self._shape = ElemShape(self.type, self.nodes)
        return self._shape

    @property
    def refs(self) -> List[Union[Elem, Beam, Plate, Pipe, Wall, Shape]]:
        return self._refs

    @property
    def formulation_override(self):
        return self._formulation_override if self._formulation_override is not None else self.type

    def update(self) -> None:
        self._nodes = list(set(self.nodes))
        if len(self.nodes) <= 1:
            self._el_id = None
        else:
            self._shape = None

    def updating_nodes(self, old_node: Node, new_node: Node) -> None:
        """Exchanging old node with new node, and updating the element shape"""
        node_index = self.nodes.index(old_node)
        self.nodes.pop(node_index)
        self.nodes.insert(node_index, new_node)

        self.update()

    def __repr__(self):
        nodes = self.nodes if hasattr(self, "_nodes") else "Nodes not yet initialized"
        return f'Elem(ID: {self._el_id}, Type: {self.type}, NodeIds: "{nodes}")'


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


class ConnectorTypes:
    BUSHING = "bushing"
    CARTESIAN = "cartesian"

    all = [BUSHING, CARTESIAN]


class Connector(Elem):
    CON_TYPES = ConnectorTypes

    def __init__(
        self,
        name,
        el_id,
        n1: Node,
        n2: Node,
        con_type,
        con_sec: "ConnectorSection",
        preload=None,
        csys: Csys = None,
        metadata=None,
        parent: "FEM" = None,
    ):
        if type(n1) is not Node or type(n2) is not Node:
            raise ValueError("Connector Start\\end must be nodes")
        self._n1 = n1
        self._n2 = n2
        self._con_type = con_type
        self._con_sec = con_sec
        self._preload = preload
        self._csys = csys if csys is not None else Csys(f"{name}_csys")

        super(Connector, self).__init__(el_id, [n1, n2], ElemType.CONNECTOR_SHAPES.CONNECTOR)
        super(Elem, self).__init__(name, metadata, parent)

    @property
    def con_type(self):
        return self._con_type

    @con_type.setter
    def con_type(self, value: str):
        self._con_type = value

    @property
    def con_sec(self) -> "ConnectorSection":
        return self._con_sec

    @con_sec.setter
    def con_sec(self, value: "ConnectorSection"):
        self._con_sec = value

    @property
    def n1(self) -> Node:
        return self._n1

    @n1.setter
    def n1(self, value: Node):
        self._n1 = value

    @property
    def n2(self) -> Node:
        return self._n2

    @n2.setter
    def n2(self, value: Node):
        self._n2 = value

    @property
    def csys(self) -> Csys:
        return self._csys

    @csys.setter
    def csys(self, value: Csys):
        self._csys = value

    def __repr__(self):
        return f'Connector(ID: {self.id}, Type: {self.type}, End1: "{self.n1}", End2: "{self.n2}")'


class Spring(Elem):
    def __init__(self, name, el_id, el_type, stiff, fem_set: "FemSet", metadata=None, parent=None):
        super(Spring, self).__init__(el_id, fem_set.members, el_type)
        super(Elem, self).__init__(name, metadata, parent)
        self._stiff = stiff
        self._n1 = fem_set.members[0]
        self._n2 = None
        if len(fem_set.members) > 1:
            self._n2 = fem_set.members[-1]
        self._fem_set = fem_set

    @property
    def fem_set(self) -> "FemSet":
        return self._fem_set

    @property
    def stiff(self):
        return self._stiff

    def __repr__(self):
        return f'Spring("{self.name}", type="{self._stiff}")'


class MassTypes:
    MASS = shape_def.MassTypes.MASS
    NONSTRU = "NONSTRUCTURAL MASS"
    ROT_INERTIA = shape_def.MassTypes.ROTARYI

    all = [MASS, NONSTRU, ROT_INERTIA]


class MassPType:
    ISOTROPIC = "ISOTROPIC"
    ANISOTROPIC = "ANISOTROPIC"

    all = [ISOTROPIC, ANISOTROPIC]


class Mass(Elem):
    TYPES = MassTypes
    PTYPES = MassPType

    def __init__(
        self,
        name,
        ref: FemSet | list[Node] | None,
        mass,
        mass_type=None,
        ptype=None,
        mass_id: int = None,
        units=None,
        metadata=None,
        parent=None,
    ):
        if hasattr(ref, "members"):
            self._fem_set = ref
            members = ref.members
        else:
            members = ref

        if mass is None:
            raise ValueError("Mass cannot be None")

        if type(mass) not in (list, tuple):
            logger.info(f"Mass {type(mass)} converted to list of len=1. Assume equal mass in get_all 3 transl. DOFs.")
            ptype = self.PTYPES.ISOTROPIC
            mass = [mass]

        self._mass = mass
        if isinstance(mass_type, str):
            mass_type = shape_def.MassTypes.from_str(mass_type)
            if mass_type is None:
                raise ValueError(f'Mass type "{self.type}" is not {shape_def.MassTypes}')

        elif mass_type is None:
            mass_type = self.TYPES.MASS

        self._el_type = mass_type

        if ptype not in MassPType.all and ptype is not None:
            raise ValueError(f'Mass point type "{ptype}" is not in list of supported types {MassPType.all}')

        super(Mass, self).__init__(mass_id, members, self.type)
        super(Elem, self).__init__(name, metadata, parent)

        self.point_mass_type = ptype
        self._units = units
        self._members = members
        self._elset = None
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
    def fem_set(self) -> FemSet:
        return self._fem_set

    @fem_set.setter
    def fem_set(self, value: FemSet):
        self._members = value.members
        self._fem_set = value

    @property
    def elset(self):
        return self._elset

    @elset.setter
    def elset(self, value):
        self._elset = value

    @property
    def mass(self):
        if self.point_mass_type is None:
            if self.type == MassTypes.MASS:
                if type(self._mass) in (list, tuple):
                    raise ValueError("Mass can only be a scalar number for Isotropic mass")
                return float(self._mass[0])
            elif self.type == MassTypes.NONSTRU:
                return self._mass
            elif self.type == MassTypes.ROT_INERTIA:
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

    @mass.setter
    def mass(self, value) -> None:
        self._mass = value

    @property
    def members(self):
        return self._members

    @property
    def units(self):
        return self._units

    @property
    def point_mass_type(self):
        return self._ptype

    @point_mass_type.setter
    def point_mass_type(self, value):
        self._ptype = value

    def __repr__(self) -> str:
        return f"Mass(ID: {self._el_id}, {self.name}, {self.point_mass_type}, [{self.mass}])"


def find_element_type_from_list(elements: List[Elem]) -> str:
    el_types = set(el.shape.elem_type_group for el in elements)
    if len(el_types) != 1:
        raise NotImplementedError("Mixed element set types as basis for surface sets is not yet supported")
    return elements[0].shape.elem_type_group
