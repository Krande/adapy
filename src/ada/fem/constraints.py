from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .common import Csys, FemBase
from .sets import FemSet
from .surfaces import Surface

if TYPE_CHECKING:
    from ada import Part

    from .base import FEM
    from .common import Amplitude


class BcTypes:
    DISPL = "displacement"
    VELOCITY = "velocity"
    CONN_DISPL = "connector_displacement"
    CONN_VEL = "connector_velocity"
    ENCASTRE = "symmetry/antisymmetry/encastre"
    DISPL_ROT = "displacement/rotation"
    VELOCITY_ANGULAR = "velocity/angular velocity"

    all = [DISPL, VELOCITY, CONN_DISPL, CONN_VEL, ENCASTRE, DISPL_ROT, VELOCITY_ANGULAR]


class PreDefTypes:
    VELOCITY = "VELOCITY"
    INITIAL_STATE = "INITIAL STATE"

    all = [VELOCITY, INITIAL_STATE]


class ConstraintTypes:
    COUPLING = "coupling"
    TIE = "tie"
    RIGID_BODY = "rigid body"
    MPC = "mpc"
    SHELL2SOLID = "shell2solid"


class Bc(FemBase):
    TYPES = BcTypes

    def __init__(
        self,
        name,
        fem_set: FemSet,
        dofs,
        magnitudes=None,
        bc_type=BcTypes.DISPL,
        amplitude: "Amplitude" = None,
        init_condition=None,
        metadata=None,
        parent=None,
    ):
        """dofs should be a list with integers from 1-6"""
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        fem_set.refs.append(self)

        self._dofs = dofs if isinstance(dofs, (list, tuple)) else [dofs]
        if magnitudes is None:
            self._magnitudes = [None] * len(self._dofs)
        else:
            self._magnitudes = magnitudes if isinstance(magnitudes, list) else [magnitudes]
        self.type = bc_type.lower()
        self._amplitude = amplitude
        self._init_condition = init_condition

    def add_init_condition(self, init_condition):
        self._init_condition = init_condition

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.lower() not in BcTypes.all:
            raise ValueError(f'BC type "{value}" is not yet supported')
        self._type = value.lower()

    @property
    def fem_set(self) -> FemSet:
        return self._fem_set

    @property
    def dofs(self):
        return self._dofs

    @property
    def magnitudes(self):
        return self._magnitudes

    @property
    def amplitude(self) -> "Amplitude":
        return self._amplitude

    def __repr__(self):
        return f'Bc("{self.name}", type="{self.type}", dofs={self.dofs}, fem_set="{self.fem_set.name}")'


class Constraint(FemBase):
    TYPES = ConstraintTypes

    def __init__(
        self,
        name,
        con_type,
        m_set: FemSet | Surface,
        s_set: FemSet | Surface,
        dofs=None,
        pos_tol=None,
        mpc_type=None,
        csys: Csys = None,
        parent=None,
        metadata=None,
        influence_distance: float = None,
    ):
        super().__init__(name, metadata, parent)
        m_set.refs.append(self)
        s_set.refs.append(self)
        self._con_type = con_type
        self._m_set = m_set
        self._s_set = s_set
        self._dofs = [1, 2, 3, 4, 5, 6] if dofs is None else dofs
        self._pos_tol = pos_tol
        self._mpc_type = mpc_type
        self._csys = csys
        self._influence_distance = influence_distance

    def switch_master_slave(self):
        from ada.fem import Surface

        if isinstance(self.s_set, Surface):
            s_set = self.s_set.fem_set
            self.s_set.fem_set = self.m_set
            self.m_set = s_set
        else:
            self.m_set, self.s_set = self.s_set, self.m_set

    @staticmethod
    def tie_fem_with_coincident_nodes(name, fem1: FEM, fem2: FEM, dofs=None) -> Constraint:
        f1_np = fem1.nodes.to_np_array(False)
        f2_np = fem2.nodes.to_np_array(False)

        # find coincident rows in the x,y,z coordinate numpy arrays f1_np and f2_np
        c2, c1 = np.where((f1_np == f2_np[:, None]).all(-1))
        n_id1 = [fem1.nodes.nodes[i] for i in c1]
        n_id2 = [fem2.nodes.nodes[i] for i in c2]
        fs1 = FemSet(f"{name}_{fem1.name}_tie_set", n_id1, FemSet.TYPES.NSET)
        fs2 = FemSet(f"{name}_{fem2.name}_tie_set", n_id2, FemSet.TYPES.NSET)
        surf1 = Surface(f"{name}_tie_surf1", Surface.TYPES.NODE, fs1)
        surf2 = Surface(f"{name}_tie_surf2", Surface.TYPES.NODE, fs2)
        return Constraint(name, ConstraintTypes.TIE, surf1, surf2, [1, 2, 3, 4, 5, 6] if dofs is None else dofs)

    @property
    def type(self):
        return self._con_type

    @property
    def m_set(self) -> FemSet | Surface:
        return self._m_set

    @m_set.setter
    def m_set(self, value: FemSet | Surface):
        self._m_set = value

    @property
    def s_set(self) -> FemSet | Surface:
        return self._s_set

    @s_set.setter
    def s_set(self, value: FemSet | Surface):
        self._s_set = value

    @property
    def dofs(self):
        return self._dofs

    @property
    def pos_tol(self):
        return self._pos_tol

    @property
    def csys(self) -> Csys:
        return self._csys

    @property
    def mpc_type(self):
        return self._mpc_type

    @property
    def influence_distance(self):
        return self._influence_distance

    def __repr__(self):
        return f'Constraint("{self.type}", m: "{self.m_set.name}", s: "{self.s_set.name}", dofs: "{self.dofs}")'


class PredefinedField(FemBase):
    TYPES = PreDefTypes

    def __init__(
        self,
        name,
        field_type,
        fem_set: FemSet = None,
        dofs=None,
        magnitude=None,
        initial_state_file=None,
        initial_state_part=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self.type = field_type
        if fem_set is not None:
            fem_set.refs.append(self)
        self._fem_set = fem_set
        self._dofs = dofs
        self._magnitude = magnitude
        self._initial_state_part = initial_state_part
        self._initial_state_file = initial_state_file
        if self.initial_state_file is not None:
            self.initial_state_part.fem.initial_state = self

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.upper() not in PreDefTypes.all:
            raise ValueError(f'The field type "{value.upper()}" is currently not supported')
        self._type = value.upper()

    @property
    def fem_set(self) -> FemSet:
        return self._fem_set

    @property
    def dofs(self):
        return self._dofs

    @property
    def magnitude(self):
        return self._magnitude

    @property
    def initial_state_part(self) -> Part:
        return self._initial_state_part

    @property
    def initial_state_file(self):
        return self._initial_state_file
