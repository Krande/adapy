"""Base service categories shared by ports and systems.

``PortCategory`` tags what a port carries (process fluid, electrical power or
signal); a ``System`` may only connect to ports of its own category. ``Voltage``
enumerates typical industrial supply levels (value in volts).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

__all__ = ["PortCategory", "Voltage"]

PortCategory = Literal["process", "electrical", "signal"]


class Voltage(Enum):
    """Typical industrial voltage levels; value in volts."""

    LV_230 = 230
    LV_400 = 400
    LV_690 = 690
    MV_3300 = 3300
    MV_6600 = 6600
    MV_11000 = 11000
