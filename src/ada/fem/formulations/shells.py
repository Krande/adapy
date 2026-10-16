from __future__ import annotations

from enum import Enum


class ShellTheory(str, Enum):
    """What a shell section carries.

    ``SHELL`` is the default: membrane and bending stiffness. ``MEMBRANE`` carries in-plane forces only -- no bending
    stiffness, and no stiffness against out-of-plane load until it is stretched. A membrane loaded out of its plane
    (a pressurised membrane cushion, an inflated tube) has to be pre-stressed or solved with geometric nonlinearity;
    a linear static step only solves it for in-plane loads.
    """

    SHELL = "shell"
    MEMBRANE = "membrane"
