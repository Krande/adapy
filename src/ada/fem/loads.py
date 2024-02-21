from __future__ import annotations

from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.config import logger

from .common import Amplitude, Csys, FemBase
from .constants import GRAVITY
from .exceptions.model_definition import UnsupportedLoadType
from .sets import FemSet
from .surfaces import Surface

if TYPE_CHECKING:
    from ada.fem.steps import Step


class LoadTypes:
    GRAVITY = "gravity"
    ACC = "acc"
    ACC_ROT = "acc_rot"
    FORCE = "force"
    FORCE_SET = "force_set"
    MASS = "mass"
    PRESSURE = "pressure"

    all = [GRAVITY, ACC, ACC_ROT, FORCE, FORCE_SET, MASS]


class Load(FemBase):
    """


    :param load_type: Type of loads. See Load.TYPES for allowable load types.
    :param magnitude: Magnitude of load
    :param name: (Required in the event of a point load being applied)
    :param fem_set: Set reference (Required in the event of a point load being applied)
    :param dof: Degrees of freedom (Required in the event of a point load being applied)
    :param follower_force: Should follower force be accounted for
    :param amplitude: Attach an amplitude object to the load
    :param accr_origin: Origin of a rotational Acceleration field (necessary for load_type='acc_rot').
    """

    TYPES = LoadTypes

    def __init__(
        self,
        name: str,
        load_type: str,
        magnitude: float,
        fem_set: FemSet = None,
        dof: Union[int, List[int]] = None,
        amplitude: Amplitude = None,
        follower_force=False,
        acc_vector=None,
        accr_origin=None,
        accr_rot_axis=None,
        csys: Csys = None,
        metadata=None,
        parent: Step = None,
    ):
        super().__init__(name, metadata, parent)
        self._type = load_type
        self._magnitude = magnitude
        self._fem_set = fem_set

        if isinstance(dof, int):
            dofs = [None, None, None, None, None, None]
            dofs[dof - 1] = 1
            self._dof = dofs
        else:
            self._dof = dof

        self._amplitude = amplitude
        self._follower_force = follower_force
        self._acc_vector = acc_vector
        self._accr_origin = accr_origin
        self._accr_rot_axis = accr_rot_axis
        self._csys = csys

        if self.type == LoadTypes.FORCE:
            if self._dof is None or self._fem_set is None or self._name is None:
                raise Exception("self._dofs and nid (Node id) and name needs to be set in order to use point loads")

            if len(self._dof) != 6:
                raise Exception(
                    "You need to include all 6 dofs even though forces are not applied in all 6 dofs. "
                    "Use None or 0.0 for the dofs not applied with forces"
                )

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.lower() not in LoadTypes.all:
            raise UnsupportedLoadType(f'Load type "{value}" is not among supported load types: "{LoadTypes.all}"')
        self._type = value

    @property
    def dof(self):
        if self._dof is None and self.type is GRAVITY:
            self._dof = [0, 0, 1] if self._dof is None else self._dof
        return self._dof

    @property
    def forces(self):
        if self.type not in (LoadTypes.FORCE,):
            return None

        return [x * self.magnitude if x is not None else 0.0 for x in self.dof]

    @property
    def forces_global(self):
        if self.type not in (LoadTypes.FORCE,):
            return None
        if self.csys is None:
            return [x * self.magnitude for x in self.dof]
        else:
            csys = self.csys
            if csys.coords is None:
                logger.error("Calculating global forces without COORDS is not yet supported")
                return None

            from ..core.vector_transforms import rotation_matrix_csys_rotate

            destination_csys = [(1, 0, 0), (0, 1, 0)]
            rmat = rotation_matrix_csys_rotate(csys.coords, destination_csys)
            res = np.concatenate([np.dot(rmat, np.array(self.dof[:3])), np.dot(rmat, np.array(self.dof[3:]))])
            return [x * self.magnitude for x in res]

    @property
    def amplitude(self):
        return self._amplitude

    @property
    def magnitude(self):
        return self._magnitude

    @property
    def fem_set(self) -> FemSet:
        return self._fem_set

    @fem_set.setter
    def fem_set(self, value: FemSet):
        self._fem_set = value

    @property
    def follower_force(self):
        return self._follower_force

    @property
    def acc_vector(self):
        if self.type not in (LoadTypes.ACC, LoadTypes.ACC_ROT):
            raise ValueError(f'Acceleration vector only applies for type "acc". Not "{self.type}"')

        dir_error = "If acc_vector is not specified, you must pass dof=[int] (int 1-3) for the acc field"

        if self._acc_vector is not None:
            return self._acc_vector

        dofs = [x for x in self.dof if x is not None]
        if len(dofs) != 1:
            raise ValueError(dir_error)

        acc_magnitude = dofs[0]
        acc_dir = int(self.dof.index(acc_magnitude) + 1)
        if 1 > acc_dir > 3:
            raise ValueError(f"Acceleration vector works only on the translational DOFs. DOF={acc_dir} was passed in")

        if acc_dir == 1:
            dvec = 1, 0, 0
        elif acc_dir == 2:
            dvec = 0, 1, 0
        else:
            dvec = 0, 0, 1

        return tuple([float(self._magnitude * d) if d != 0 else 0.0 for d in dvec])

    @property
    def acc_rot_origin(self):
        return self._accr_origin

    @acc_rot_origin.setter
    def acc_rot_origin(self, value):
        self._accr_origin = value

    @property
    def acc_rot_axis(self):
        return self._accr_rot_axis

    @property
    def csys(self) -> Csys:
        return self._csys

    @property
    def parent(self) -> "Step":
        return self._parent

    @parent.setter
    def parent(self, value: "Step"):
        self._parent = value

    def __repr__(self):
        forc_str = ",".join(f"{f:.6E}" for f in self.forces)
        return f"Load({self.name}, {self.type}, [{forc_str}])"


class PressureDistribution:
    UNIFORM = "uniform"
    TOTAL_FORCE = "total_force"


class LoadPressure(Load):
    P_DIST_TYPES = PressureDistribution

    def __init__(self, name: str, magnitude: float, surface: Surface, distribution=P_DIST_TYPES.UNIFORM):
        super(LoadPressure, self).__init__(name, LoadTypes.PRESSURE, magnitude)
        self._surface = surface
        self._distribution = distribution

    @property
    def surface(self) -> Surface:
        return self._surface

    @property
    def distribution(self) -> str:
        return self._distribution


class LoadGravity(Load):
    def __init__(self, name, acceleration=-9.81):
        super(LoadGravity, self).__init__(name, Load.TYPES.GRAVITY, acceleration)


class LoadPoint(Load):
    def __init__(self, name, magnitude, fem_set, dof, amplitude=None, follower_force=True, csys=None):
        super(LoadPoint, self).__init__(
            name,
            Load.TYPES.FORCE,
            magnitude=magnitude,
            fem_set=fem_set,
            dof=dof,
            amplitude=amplitude,
            follower_force=follower_force,
            csys=csys,
        )


class LoadCase(FemBase):
    def __init__(
        self,
        name,
        comment,
        loads=None,
        mass=None,
        lcsys=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._comment = comment
        self._loads = loads
        self._mass = mass
        self._lcsys = lcsys

    @property
    def loads(self):
        return self._loads

    @property
    def mass(self):
        return self._mass

    @property
    def comment(self):
        return self._comment

    @property
    def csys(self):
        return (1, 0, 0), (0, 1, 0), (0, 0, 1) if self._lcsys is None else self._lcsys

    def __repr__(self):
        return f"LC({self.name}, {self.comment})"
