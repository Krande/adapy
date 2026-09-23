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
    EQUATION = "equation"


ALL_DOFS = (1, 2, 3, 4, 5, 6)


def expand_dofs(dofs) -> tuple[int, ...]:
    """Normalise the several shapes a DOF specification arrives in into sorted unique ints.

    Constraints collect their DOFs from wherever they were read, and the shapes genuinely differ:
    the Abaqus reader hands over an ``(n, 2)`` numpy array of ``(first, last)`` ranges straight
    from a ``*Kinematic`` block, hand-built models pass ``[1, 2, 3]``, and a single DOF is
    sometimes just ``4``.

    The one ambiguity worth spelling out: a *flat* sequence is a list of DOFs, while a *nested*
    one is a list of ranges. So ``[1, 3]`` means DOFs 1 and 3, and ``[[1, 3]]`` means 1 through 3.
    Anything outside 1-6, and anything that is not an integer, raises ``ValueError``: that is
    caller error rather than a lossy conversion, and quietly dropping it would write a model
    nobody asked for.

    ``None`` means "not declared", which for every constraint type in ada means all six.
    """
    if dofs is None:
        return ALL_DOFS

    if isinstance(dofs, np.ndarray):
        dofs = dofs.tolist()
    elif isinstance(dofs, (int, np.integer)) and not isinstance(dofs, bool):
        dofs = [int(dofs)]

    if not isinstance(dofs, (list, tuple, set, frozenset)):
        raise ValueError(f"cannot read a DOF specification from {dofs!r}")

    def as_dof(value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"DOF {value!r} is not an integer")
        value = int(value)
        if not 1 <= value <= 6:
            raise ValueError(f"DOF {value} is outside 1-6")
        return value

    found: set[int] = set()
    for entry in dofs:
        if isinstance(entry, np.ndarray):
            entry = entry.tolist()
        if isinstance(entry, (list, tuple)):
            if len(entry) != 2:
                raise ValueError(f"a DOF range must be (first, last), got {entry!r}")
            first, last = as_dof(entry[0]), as_dof(entry[1])
            if last < first:
                raise ValueError(f"a DOF range must not run backwards, got {entry!r}")
            found.update(range(first, last + 1))
        else:
            found.add(as_dof(entry))

    return tuple(sorted(found))


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
        equation_terms=None,
    ):
        super().__init__(name, metadata, parent)
        m_set.refs.append(self)
        s_set.refs.append(self)
        self._con_type = con_type
        self._m_set = m_set
        self._s_set = s_set
        self._dofs_declared = dofs is not None
        self._dofs = [1, 2, 3, 4, 5, 6] if dofs is None else dofs
        self._pos_tol = pos_tol
        self._mpc_type = mpc_type
        self._csys = csys
        self._influence_distance = influence_distance
        self._equation_terms = None if equation_terms is None else tuple(tuple(t) for t in equation_terms)

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
    def dofs_declared(self) -> bool:
        """Whether the caller (or the deck) actually said which DOFs this constraint holds.

        ``dofs`` cannot answer this on its own: ``_dofs`` has stored ``[1, 2, 3, 4, 5, 6]`` for
        an omitted specification since long before any writer looked at it, so "all six" and
        "unstated" are the same value there — and that storage is relied on elsewhere, so it is
        left alone.

        The distinction matters to a writer that honours ``dofs``. An *explicit* all-six is a
        statement: Abaqus' ``*Kinematic`` with no data lines means every DOF, and the Abaqus
        reader hands that over as ``[[1, 6]]``. An *omitted* one is merely the default of an
        argument, and taking it literally would silently change what every hand-built
        ``Constraint`` in existing code writes. See
        ``ada.fem.formats.sesam.write.write_constraints._constraint_dofs``.
        """
        return self._dofs_declared

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

    @property
    def equation_terms(self):
        """For ``EQUATION`` constraints: the terms of a linear multi-point constraint.

        A tuple of ``(node, dof, coefficient)``, in the order the deck declared them. Abaqus'
        convention is that the **first** term is the one eliminated, so it names the dependent
        DOF and every later term is an independent contribution; the writers rely on that order,
        so it is preserved rather than sorted.

        ``None`` for every other constraint type.
        """
        return self._equation_terms

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
