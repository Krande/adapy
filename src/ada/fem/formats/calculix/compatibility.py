from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: Assembly):
    """Nothing to refuse up front any more.

    This refused general-section (``U1``) beams in a frequency step and under gravity. Both were stale: measured with
    ccx 2.23, a 4 m U1 cantilever of an IPE300 in 32 elements has its first two frequencies at 6.16414 and 22.4099 Hz
    against Euler-Bernoulli's 6.16561 and 22.4616 (U1's mass matrix carries rotary inertia), and the weight of U1 beams,
    which take no body force, is written as nodal loads (``write_loads.gravity_load_str``).
    """
    return None
