"""An interned Point or Direction holds +0.0, whatever sign its zeros came in with and whatever was built before.

``-0.0 == 0.0`` and both hash alike, so the value interning of :class:`~ada.geom.points.Point` and
:class:`~ada.geom.direction.Direction` handed a ``(0, 0, 1)`` whichever of it and ``(-0, -0, 1)`` had been interned
first and was still alive. Measured: a GeniE plate with a hole wrote its normal ``x="-0.0" y="-0.0"`` after
``test_gxml_read_supports`` (whose vertical beams intern ``Direction(-0, -0, 1)``, kept alive by the lru cache of
``get_computed_placement_cached``) and ``x="0.0" y="0.0"`` alone; clearing ``Direction._cache`` before the write made
it pass, clearing the lru caches did not.
"""

from __future__ import annotations

import math

import pytest

from ada.api.computed_placement import create_computed_placement_from_placement
from ada.core.vector_utils import unit_vector
from ada.geom.direction import Direction
from ada.geom.points import Point

NEG, POS = (-0.0, -0.0, 1.0), (0.0, 0.0, 1.0)


def _negative_zeros(values) -> list[bool]:
    return [float(v) == 0.0 and math.copysign(1.0, float(v)) < 0 for v in values]


@pytest.mark.parametrize("cls", [Direction, Point])
@pytest.mark.parametrize("first, then", [(NEG, POS), (POS, NEG)], ids=["signed first", "plain first"])
def test_an_interned_value_holds_positive_zeros_whatever_came_first(cls, first, then):
    held = cls(*first)  # alive while the second is made: the intern table is weak
    got = cls(*then)
    assert got is held
    assert _negative_zeros(held) == _negative_zeros(got) == [False, False, False]


@pytest.mark.parametrize(
    "fn",
    [
        lambda v: unit_vector(v),
        lambda z: create_computed_placement_from_placement((1.0, 0.0, 0.0), None, z).zdir,
    ],
    ids=["unit_vector", "computed_placement"],
)
def test_a_cached_direction_holds_positive_zeros_in_either_call_order(fn):
    """These lru caches take -0.0 and 0.0 for one key too; what they hand out is a Direction, so it is +0.0 either
    way."""
    for v in (NEG, POS, NEG):
        assert _negative_zeros(fn(v)) == [False, False, False]
