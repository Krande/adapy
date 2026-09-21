"""Differential test for the fast path in ``Placement.is_identity``.

``is_identity`` used to build a throwaway identity ``Placement`` -- four
interned ``Direction`` lookups -- and compare against it through
``Placement.__eq__``, which allocates one interned ``Point``/``Direction``
difference per axis just to take its length. That was ~35 us a call, on a
method called once per beam, plate and primitive on the way to geometry. It
now answers the same question on plain floats and only falls back to ``__eq__``
where that reasoning does not apply.

Which makes it a pure-performance change, so the pre-optimisation
implementation is kept here verbatim as ``_reference_is_identity`` and the two
are asserted to agree -- ``is`` on the booleans, not merely truthiness -- over
a spread built to hit the places where a plausible rewrite would have drifted:

* ``norm(v) > 0.0`` is not ``any(c != 0 for c in v)``. A NaN component makes
  the norm NaN, and ``NaN > 0.0`` is *false*, so the old code called a NaN
  origin identical to the zero one. Components below ~1e-162 square to zero,
  so the old code called those identical too. Both are preserved here.
* Near-identity frames and origins at 1e-9 and 1e-15, where a tolerance-based
  rewrite would answer differently from the exact comparison actually in use.
* Ancestry: the absolute placement is what ``use_absolute_placement=True``
  tests, so an identity placement under a translated parent must still come
  back non-identity, and both flags are checked on every case.
* The fallback itself -- an origin that is not a ``Point`` -- so the path that
  still goes through ``__eq__`` stays covered.
"""

from __future__ import annotations

import numpy as np
import pyquaternion as pq
import pytest

import ada
from ada import Placement
from ada.geom.placement import XV, YV, ZV, O


def _reference_is_identity(place: Placement, use_absolute_placement: bool = True) -> bool:
    """The pre-optimisation ``Placement.is_identity``, transcribed unchanged."""
    p = place.get_absolute_placement() if use_absolute_placement else place
    return p == Placement(O(), XV(), YV(), ZV())


def _check(place: Placement, ctx: str) -> None:
    for use_absolute in (True, False):
        expected = _reference_is_identity(place, use_absolute)
        actual = place.is_identity(use_absolute)
        assert actual is expected, f"{ctx} use_absolute_placement={use_absolute}: {actual!r} != {expected!r}"
        # ...and again, in case anything ever starts memoising the answer.
        assert place.is_identity(use_absolute) is expected, f"{ctx} (repeat) use_absolute_placement={use_absolute}"


def _translation_matrix_4x4(translation) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = translation
    return m


def _with_raw_origin(origin) -> Placement:
    """A placement whose origin is a bare ndarray -- the ``__eq__`` fallback."""
    place = Placement()
    place.origin = np.asarray(origin, dtype=float)
    return place


CASES: dict[str, callable] = {
    "default": Placement,
    "explicit-zero-origin": lambda: Placement(origin=(0, 0, 0)),
    "explicit-global-axes": lambda: Placement(origin=(0, 0, 0), xdir=(1, 0, 0), ydir=(0, 1, 0), zdir=(0, 0, 1)),
    "point-origin": lambda: Placement(origin=ada.Point(0, 0, 0)),
    "signed-zero-origin": lambda: Placement(origin=(-0.0, 0.0, -0.0)),
    # Smallest subnormal and 1e-200: both square to exactly 0.0, so the length
    # of the difference is 0.0 and the old code called them identity.
    "subnormal-origin": lambda: Placement(origin=(5e-324, 0, 0)),
    "underflowing-origin": lambda: Placement(origin=(1e-200, 0, 0)),
    "origin-1e-15": lambda: Placement(origin=(1e-15, -1e-15, 1e-15)),
    "origin-1e-9": lambda: Placement(origin=(0, 1e-9, 0)),
    "origin-1e-6": lambda: Placement(origin=(1e-6, 0, 0)),
    "translated": lambda: Placement(origin=(1.0, 2.0, 3.0)),
    "translated-far": lambda: Placement(origin=(1e150, 0, 0)),
    # NaN: the norm is NaN and ``NaN > 0.0`` is false, so this is "identity".
    "nan-origin": lambda: Placement(origin=(float("nan"), 0, 0)),
    "nan-origin-all": lambda: Placement(origin=(float("nan"),) * 3),
    "inf-origin": lambda: Placement(origin=(float("inf"), 0, 0)),
    "rotated-90-z": lambda: Placement.from_axis_angle([0, 0, 1], 90.0),
    "rotated-30-x": lambda: Placement.from_axis_angle([1, 0, 0], 30.0),
    "rotated-1e-9-deg": lambda: Placement.from_axis_angle([0, 0, 1], 1e-9),
    "rotated-1e-15-deg": lambda: Placement.from_axis_angle([0, 0, 1], 1e-15),
    "rotated-360-deg": lambda: Placement.from_axis_angle([0, 0, 1], 360.0),
    "rotated-and-translated": lambda: Placement.from_axis_angle([0, 1, 0], 45.0, origin=(1, 0, 0)),
    # ``Placement(xdir=...)`` alone trips an unrelated bug in
    # ``compute_orientation_vec`` (it leaves the derived yvec a bare ndarray),
    # so the partially specified frames here always pass a zdir as well.
    "xdir-zdir-global": lambda: Placement(xdir=(1, 0, 0), zdir=(0, 0, 1)),
    "xdir-flipped": lambda: Placement(xdir=(-1, 0, 0), zdir=(0, 0, 1)),
    "xdir-near-global": lambda: Placement(xdir=(1, 1e-14, 0), zdir=(0, 0, 1)),
    "xdir-near-global-1e-9": lambda: Placement(xdir=(1, 1e-9, 0), zdir=(0, 0, 1)),
    "xdir-tilted": lambda: Placement(xdir=(1, 1, 0), zdir=(0, 0, 1)),
    "zdir-only": lambda: Placement(zdir=(0, 0, 1)),
    "zdir-flipped": lambda: Placement(zdir=(0, 0, -1)),
    "unnormalized-global-axes": lambda: Placement(xdir=(2, 0, 0), ydir=(0, 2, 0), zdir=(0, 0, 2)),
    "from-4x4-identity": lambda: Placement.from_4x4_matrix(np.eye(4)),
    "from-4x4-translated": lambda: Placement.from_4x4_matrix(_translation_matrix_4x4((1.0, 0.0, 0.0))),
    "from-quaternion-identity": lambda: Placement.from_quaternion(pq.Quaternion()),
    "raw-ndarray-zero-origin": lambda: _with_raw_origin((0.0, 0.0, 0.0)),
    "raw-ndarray-tiny-origin": lambda: _with_raw_origin((1e-200, 0.0, 0.0)),
    "raw-ndarray-nan-origin": lambda: _with_raw_origin((float("nan"), 0.0, 0.0)),
    "raw-ndarray-translated": lambda: _with_raw_origin((0.0, 0.5, 0.0)),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_fast_path_matches_the_reference(name):
    _check(CASES[name](), name)


@pytest.mark.parametrize("name", sorted(CASES))
def test_fast_path_matches_the_reference_under_ancestry(name):
    """The same placements, resolved through a part hierarchy.

    ``use_absolute_placement=True`` asks about the accumulated placement, so
    each case is hung under an identity parent (where the answer must not
    change) and under a translated one (where the absolute answer must flip to
    non-identity while the local one does not).
    """
    for part_origin in ((0, 0, 0), (0.0, 0.0, 2.5)):
        a = ada.Assembly("A")
        part = ada.Part("P", placement=Placement(origin=part_origin))
        a.add_part(part)
        bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", placement=CASES[name]())
        part.add_beam(bm)

        _check(bm.placement, f"{name} under part at {part_origin}")
        _check(part.placement, f"part at {part_origin}")


def test_identity_beam_under_a_translated_part_is_not_absolutely_identity():
    """A sanity anchor, so the differential test cannot pass by agreeing on nonsense."""
    a = ada.Assembly("A")
    part = ada.Part("P", placement=Placement(origin=(0.0, 0.0, 2.5)))
    a.add_part(part)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    part.add_beam(bm)

    assert bm.placement.is_identity(use_absolute_placement=False) is True
    assert bm.placement.is_identity(use_absolute_placement=True) is False
    assert Placement().is_identity() is True
    assert Placement(origin=(1, 0, 0)).is_identity() is False


def test_rounding_falls_back_to_the_reference():
    """With ``precision`` set, the difference is rounded before its length is taken.

    That can call a non-zero difference zero, which the float fast path does
    not model -- so it must decline and let ``__eq__`` answer.
    """
    from ada import Direction, Point

    place = Placement(origin=(1e-9, 0, 0))
    assert place._is_identity_on_floats() is False

    old_point, old_direction = Point.precision, Direction.precision
    try:
        Point.precision = 6
        Direction.precision = 6
        assert place._is_identity_on_floats() is None
        _check(place, "rounded")
    finally:
        Point.precision = old_point
        Direction.precision = old_direction


def test_two_dimensional_origin_fails_the_same_way():
    """A 2D origin was never supported; the fallback must not quietly change that."""
    place = Placement(origin=(0, 0))
    assert place._is_identity_on_floats() is None
    with pytest.raises(ValueError):
        _reference_is_identity(place)
    with pytest.raises(ValueError):
        place.is_identity()
