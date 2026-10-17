from __future__ import annotations

from enum import Enum


class BeamTheory:
    TIMOSHENKO = "timoshenko"
    EULER_BERNOULLI = "euler_bernoulli"


class LineTheory(str, Enum):
    """What a line section carries.

    ``BEAM`` is the default: axial force, shear, bending and torsion. ``TRUSS`` carries axial force only, in tension
    and in compression. ``CABLE`` carries axial force in tension only -- it goes slack under compression. Truss and
    cable sections take their area from the section's ``properties.Ax`` and need no orientation.
    """

    BEAM = "beam"
    TRUSS = "truss"
    CABLE = "cable"

    @property
    def is_axial_only(self) -> bool:
        return self in (LineTheory.TRUSS, LineTheory.CABLE)
