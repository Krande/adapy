"""Differential test for the fast paths in ``Placement.get_absolute_placement``.

``get_absolute_placement`` used to walk the owner's whole ancestry on every
call, re-summing the same origins and re-multiplying the same rotation
matrices once per element rather than once per placement. It now collapses
that walk when the element's own placement is the local identity, and memoises
the accumulation behind an object-identity fingerprint of everything it read.

Both are only legitimate if they are *exactly* equivalent, so this file keeps
the pre-optimisation algorithm around verbatim as ``_reference_absolute`` and
asserts bit-for-bit equality against it over a deliberately degeneracy-biased
spread: zero and signed-zero origins, subnormal and astronomically large
coordinates, axis-aligned and anti-aligned frames, near-parallel axes, mixed
units, and nesting from one to six levels deep. Bit-for-bit and not
``approx``: a wrong answer here has historically been a wrong *unit* or a
transposed frame, and a loose assertion would wave both through.

The second half covers invalidation. A cache that survived a mutation to an
ancestor's transform would be a far worse bug than the cost it saves, so every
route that can change the answer -- an origin reassigned, a placement
replaced, an element or an intermediate part re-parented -- is exercised as
read / mutate / read and re-checked against the reference.
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

import ada
from ada import Direction, Placement, Point
from ada.core.vector_utils import (
    is_exact_identity_rot_matrix,
    is_identity_rot_matrix,
    scalars_are_close,
    vectors_are_close,
)


def _reference_absolute(place: Placement, include_rotations: bool) -> Placement:
    """The pre-optimisation ``get_absolute_placement``, transcribed unchanged."""
    if place.parent is None:
        return place

    current_location = place.origin.copy()

    if include_rotations:
        accumulated_rot_matrix = place.rot_matrix.copy()
        ancestry = place.parent.get_ancestors(include_self=False)

        for ancestor in ancestry:
            current_location += ancestor.placement.origin
            accumulated_rot_matrix = ancestor.placement.rot_matrix @ accumulated_rot_matrix

        return Placement(
            origin=current_location,
            xdir=accumulated_rot_matrix[0],
            ydir=accumulated_rot_matrix[1],
            zdir=accumulated_rot_matrix[2],
        )

    ancestry = place.parent.get_ancestors(include_self=False)
    for ancestor in ancestry:
        current_location += ancestor.placement.origin

    return Placement(origin=current_location, xdir=place.xdir, ydir=place.ydir, zdir=place.zdir)


def _assert_identical(actual: Placement, expected: Placement, ctx: str) -> None:
    """Bit-for-bit, via the raw doubles -- not ``allclose``."""
    for attr in ("origin", "xdir", "ydir", "zdir"):
        got = np.asarray(getattr(actual, attr), dtype=float)
        want = np.asarray(getattr(expected, attr), dtype=float)
        assert got.tobytes() == want.tobytes(), f"{ctx}: {attr} {got!r} != {want!r}"
    assert actual.rot_matrix.tobytes() == expected.rot_matrix.tobytes(), f"{ctx}: rot_matrix"


def _check(place: Placement, ctx: str) -> None:
    for include_rotations in (False, True):
        expected = _reference_absolute(place, include_rotations)
        actual = place.get_absolute_placement(include_rotations=include_rotations)
        _assert_identical(actual, expected, f"{ctx} include_rotations={include_rotations}")
        # ... and again, so a memo that answers a second call cannot drift.
        actual_again = place.get_absolute_placement(include_rotations=include_rotations)
        _assert_identical(actual_again, expected, f"{ctx} (repeat) include_rotations={include_rotations}")


# --------------------------------------------------------------------------
# input spread
# --------------------------------------------------------------------------

DEGENERATE_ORIGINS = [
    None,
    (0, 0, 0),
    (-0.0, 0.0, -0.0),
    (5e-324, 0, 0),  # smallest subnormal
    (1e-15, -1e-15, 1e-15),
    (1e12, -1e12, 1e12),
    (1e150, 0, 0),  # far outside any physical model extent
    (0.001, 0.002, 0.003),
    (-5.5, 3.25, -0.125),
    (1000.0, 0.0, 0.0),
]

DEGENERATE_AXES = [
    None,
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),  # explicit global frame
    ((1, 0, 0), None, (0, 0, 1)),
    ((0, 0, 1), None, (1, 0, 0)),
    ((-1, 0, 0), None, (0, 0, 1)),  # anti-aligned
    ((0, 1, 0), None, (0, 0, -1)),
    ((1, 1, 0), None, (0, 0, 1)),
    ((1, 1e-14, 0), None, (0, 0, 1)),  # near-parallel to global X
    ((1, 0, 0), None, (0, 1e-14, 1)),  # near-degenerate up
]


def _placement(origin, axes) -> Placement:
    if axes is None:
        return Placement(origin=origin)
    xdir, ydir, zdir = axes
    return Placement(origin=origin, xdir=xdir, ydir=ydir, zdir=zdir)


def _build(depth: int, origin, axes, units="m") -> tuple[ada.Assembly, ada.Part, ada.Beam, ada.Beam, ada.Plate]:
    """Nest ``depth`` parts, alternating the awkward placement with a plain one."""
    a = ada.Assembly("A", units=units)
    node = a
    for level in range(depth):
        placement = _placement(origin, axes) if level % 2 == 0 else Placement()
        part = ada.Part(f"P{level}", placement=placement)
        node.add_part(part)
        node = part

    plain = ada.Beam("plain", (0, 0, 0), (1, 0, 0), "IPE300")
    node.add_beam(plain)
    placed = ada.Beam("placed", (0, 0, 0), (0, 0, 1), "IPE300", placement=_placement(origin, axes))
    node.add_beam(placed)
    plate = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    node.add_plate(plate)
    return a, node, plain, placed, plate


@pytest.mark.parametrize("depth", [1, 2, 3, 6])
@pytest.mark.parametrize("origin_idx", range(len(DEGENERATE_ORIGINS)))
@pytest.mark.parametrize("axes_idx", range(len(DEGENERATE_AXES)))
def test_degenerate_placements_match_the_reference_bit_for_bit(depth, origin_idx, axes_idx):
    origin = DEGENERATE_ORIGINS[origin_idx]
    axes = DEGENERATE_AXES[axes_idx]
    _, part, plain, placed, plate = _build(depth, origin, axes)

    ctx = f"depth={depth} origin={origin} axes={axes}"
    _check(part.placement, f"{ctx} part")
    _check(plain.placement, f"{ctx} plain-beam")
    _check(placed.placement, f"{ctx} placed-beam")
    _check(plate.placement, f"{ctx} plate")


def _random_origin(rng: random.Random):
    scale = rng.choice([5e-324, 1e-12, 1e-6, 1e-3, 1.0, 1e3, 1e9, 1e15])
    return tuple(rng.uniform(-1, 1) * scale for _ in range(3))


def _random_axes(rng: random.Random):
    roll = rng.random()
    if roll < 0.25:
        return None
    if roll < 0.55:
        return rng.choice(
            [
                ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                ((1, 0, 0), None, (0, 0, 1)),
                ((0, 1, 0), None, (0, 0, 1)),
                ((0, 0, 1), None, (1, 0, 0)),
                ((-1, 0, 0), None, (0, 0, 1)),
                ((0, -1, 0), None, (0, 0, -1)),
            ]
        )
    axis = [rng.uniform(-1, 1) for _ in range(3)]
    norm = math.sqrt(sum(c * c for c in axis)) or 1.0
    axis = [c / norm for c in axis]
    rotated = Placement.from_axis_angle(axis, rng.uniform(-360.0, 360.0))
    return (tuple(rotated.xdir), tuple(rotated.ydir), tuple(rotated.zdir))


def test_randomised_hierarchies_match_the_reference_bit_for_bit():
    rng = random.Random(20240607)
    for case in range(400):
        depth = rng.randint(1, 4)
        units = rng.choice(["m", "mm"])
        a = ada.Assembly(f"R{case}", units=units)
        node = a
        for level in range(depth):
            node.add_part(ada.Part(f"P{level}", placement=_placement(_random_origin(rng), _random_axes(rng))))
            node = node.parts[f"P{level}"]

        plain = ada.Beam("plain", (0, 0, 0), (rng.uniform(0.1, 10), 0, 0), "IPE300")
        node.add_beam(plain)
        placed = ada.Beam(
            "placed",
            (0, 0, 0),
            (0, rng.uniform(0.1, 10), 0),
            "IPE300",
            placement=_placement(_random_origin(rng), _random_axes(rng)),
        )
        node.add_beam(placed)

        _check(node.placement, f"case={case} part")
        _check(plain.placement, f"case={case} plain-beam")
        _check(placed.placement, f"case={case} placed-beam")


# --------------------------------------------------------------------------
# invalidation
# --------------------------------------------------------------------------


def test_mutating_an_ancestor_origin_invalidates_the_resolved_placement():
    a = ada.Assembly("A")
    outer = ada.Part("outer", placement=Placement(origin=(1, 2, 3)))
    a.add_part(outer)
    inner = ada.Part("inner", placement=Placement(origin=(10, 0, 0)))
    outer.add_part(inner)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    inner.add_beam(bm)

    _check(bm.placement, "before")
    assert bm.placement.get_absolute_placement().origin.tolist() == [11.0, 2.0, 3.0]

    outer.placement.origin = Point(5, 5, 5)
    _check(bm.placement, "after ancestor origin change")
    assert bm.placement.get_absolute_placement().origin.tolist() == [15.0, 5.0, 5.0]


def test_replacing_an_ancestor_placement_invalidates_the_resolved_placement():
    """The ``placement`` setter leaves the new placement unparented.

    An unparented placement resolves to *itself* rather than to an
    accumulation, so an element must not be allowed to defer to its
    container's answer in that state. Nested two deep on purpose: with the
    container sitting directly under the assembly, the container's own origin
    is the whole answer and a wrong deferral would coincide with the right one.
    """
    a = ada.Assembly("A")
    outer = ada.Part("outer", placement=Placement(origin=(1, 2, 3)))
    a.add_part(outer)
    inner = ada.Part("inner", placement=Placement(origin=(10, 0, 0)))
    outer.add_part(inner)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    inner.add_beam(bm)

    _check(bm.placement, "before")
    assert bm.placement.get_absolute_placement().origin.tolist() == [11.0, 2.0, 3.0]

    inner.placement = Placement(origin=(20, 0, 0))
    assert inner.placement.parent is None  # the setter does not re-parent
    _check(bm.placement, "after ancestor placement replaced")
    assert bm.placement.get_absolute_placement().origin.tolist() == [21.0, 2.0, 3.0]

    # ... and once the new placement IS parented, the collapse is live again.
    inner.placement.parent = inner
    _check(bm.placement, "after ancestor placement re-parented")
    assert bm.placement.get_absolute_placement().origin.tolist() == [21.0, 2.0, 3.0]


def test_reparenting_an_element_invalidates_the_resolved_placement():
    a = ada.Assembly("A")
    left = ada.Part("left", placement=Placement(origin=(1, 0, 0)))
    right = ada.Part("right", placement=Placement(origin=(0, 7, 0)))
    a.add_part(left)
    a.add_part(right)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    left.add_beam(bm)

    _check(bm.placement, "in left")
    assert bm.placement.get_absolute_placement().origin.tolist() == [1.0, 0.0, 0.0]

    right.add_beam(bm)
    _check(bm.placement, "in right")
    assert bm.placement.get_absolute_placement().origin.tolist() == [0.0, 7.0, 0.0]


def test_reparenting_an_intermediate_part_invalidates_the_resolved_placement():
    a = ada.Assembly("A")
    top = ada.Part("top", placement=Placement(origin=(100, 0, 0)))
    a.add_part(top)
    middle = ada.Part("middle", placement=Placement(origin=(0, 3, 0)))
    top.add_part(middle)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    middle.add_beam(bm)

    _check(bm.placement, "under top")
    assert bm.placement.get_absolute_placement().origin.tolist() == [100.0, 3.0, 0.0]

    a.add_part(middle)  # middle moves out from under top
    _check(bm.placement, "under assembly")
    assert bm.placement.get_absolute_placement().origin.tolist() == [0.0, 3.0, 0.0]


def test_replacing_the_elements_own_placement_invalidates_the_resolved_placement():
    a = ada.Assembly("A")
    part = ada.Part("part", placement=Placement(origin=(1, 2, 3)))
    a.add_part(part)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    part.add_beam(bm)

    _check(bm.placement, "identity own placement")
    bm.placement = Placement(origin=(0.5, 0, 0))
    bm.placement.parent = bm
    _check(bm.placement, "own placement replaced")
    assert bm.placement.get_absolute_placement().origin.tolist() == [1.5, 2.0, 3.0]

    bm.placement.origin = Point(0, 0, 7)
    _check(bm.placement, "own origin mutated")
    assert bm.placement.get_absolute_placement().origin.tolist() == [1.0, 2.0, 10.0]


def test_an_ndarray_origin_is_never_memoised():
    """A raw ndarray origin is writable, so it must not be cached behind us."""
    a = ada.Assembly("A")
    part = ada.Part("part", placement=Placement(origin=(1, 2, 3)))
    a.add_part(part)
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300", placement=Placement(origin=(1, 0, 0)))
    part.add_beam(bm)

    part.placement.origin = np.array([4.0, 0.0, 0.0])
    _check(bm.placement, "ndarray ancestor origin")
    assert bm.placement.get_absolute_placement().origin.tolist() == [5.0, 0.0, 0.0]

    part.placement.origin[0] = 40.0  # in-place write a fingerprint could not see
    assert bm.placement.get_absolute_placement().origin.tolist() == [41.0, 0.0, 0.0]


def test_cog_is_unchanged_by_the_fast_paths():
    """End-to-end: the COG of a nested, partly rotated model."""
    a = ada.Assembly("A")
    for i in range(3):
        area = ada.Part(f"area{i}", placement=Placement(origin=(i * 10.0, 0, 0)))
        a.add_part(area)
        deck = ada.Part(f"deck{i}", placement=Placement.from_axis_angle([0, 0, 1], 30.0, origin=(0, 0, 4.0)))
        area.add_part(deck)
        for j in range(4):
            deck.add_beam(ada.Beam(f"bm{i}_{j}", (j, 0, 0), (j + 1, 0, 0), "IPE300"))

    cog = a.calculate_cog()
    expected = np.zeros(3)
    total = 0.0
    for bm in a.get_all_physical_objects(by_type=ada.Beam):
        place_abs = _reference_absolute(bm.placement, True)
        start = place_abs.transform_array_from_other_place(np.asarray([bm.n1.p]), Placement())[0]
        end = place_abs.transform_array_from_other_place(np.asarray([bm.n2.p]), Placement())[0]
        mass = bm.section.properties.Ax * float(np.linalg.norm(end - start)) * bm.material.model.rho
        expected += 0.5 * (start + end) * mass
        total += mass

    assert np.asarray(cog.p).tobytes() == (expected / total).tobytes()


# --------------------------------------------------------------------------
# the numpy-semantics helpers
# --------------------------------------------------------------------------

_EDGE_SCALARS = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    1e-9,
    1e-8,
    1.1e-8,
    1e-5,
    5e-324,
    1e-300,
    1e300,
    1.0 + 1e-9,
    1.0 + 1e-5,
    1.0 - 1e-5,
    math.inf,
    -math.inf,
    math.nan,
]


@pytest.mark.parametrize("x", _EDGE_SCALARS)
@pytest.mark.parametrize("y", _EDGE_SCALARS)
def test_scalars_are_close_matches_numpy(x, y):
    assert scalars_are_close(x, y) == bool(np.isclose(x, y))


@pytest.mark.parametrize("magnitude", [1e-9, 1e-6, 1e-3, 1.0, 1e3, 1e6, 1e12])
def test_scalars_are_close_matches_numpy_on_the_tolerance_boundary(magnitude):
    """Straddle the boundary in both argument orders.

    ``np.isclose`` scales ``rtol`` by the SECOND argument only, so the
    predicate is asymmetric. Separations pitched either side of the boundary
    resolve differently depending on which value carries the tolerance, which
    is what pins the asymmetry down rather than merely the tolerance size.
    """
    boundary = 1e-08 + 1e-05 * magnitude
    for scale in (0.5, 0.9, 0.999999, 1.0, 1.000001, 1.0000001, 1.1, 2.0):
        separation = boundary * scale
        for x, y in (
            (magnitude, magnitude + separation),
            (magnitude + separation, magnitude),
            (-magnitude, -magnitude - separation),
            (-magnitude - separation, -magnitude),
            (magnitude, -magnitude),
        ):
            assert scalars_are_close(x, y) == bool(np.isclose(x, y)), (x, y)
            assert vectors_are_close((x, x, x), (y, y, y)) == bool(np.allclose((x, x, x), (y, y, y)))


def test_vectors_are_close_matches_numpy_over_a_random_spread():
    rng = np.random.default_rng(11)
    pool = np.array(_EDGE_SCALARS)
    for _ in range(4000):
        if rng.random() < 0.5:
            a = rng.choice(pool, 3)
            b = rng.choice(pool, 3)
        else:
            a = rng.normal(size=3) * 10.0 ** rng.integers(-12, 12)
            b = a + rng.normal(size=3) * 10.0 ** rng.integers(-14, 2)
        assert vectors_are_close(a, b) == bool(np.allclose(a, b))


def test_is_identity_rot_matrix_matches_numpy_over_a_random_spread():
    rng = np.random.default_rng(12)
    identity = np.identity(3)
    assert is_identity_rot_matrix(identity)
    for _ in range(4000):
        m = identity + rng.normal(size=(3, 3)) * 10.0 ** rng.integers(-14, 2)
        assert is_identity_rot_matrix(m) == bool(np.allclose(m, identity))
    for scale in (0.0, 1e-12, 1e-9, 1e-8, 1e-6, 1e-4):
        m = identity + scale
        assert is_identity_rot_matrix(m) == bool(np.allclose(m, identity))


def test_is_exact_identity_rot_matrix_is_exact():
    identity = np.identity(3)
    assert is_exact_identity_rot_matrix(identity)
    assert is_exact_identity_rot_matrix(Placement().rot_matrix)
    nudged = identity.copy()
    nudged[0, 1] = 5e-324
    assert not is_exact_identity_rot_matrix(nudged)
    assert is_identity_rot_matrix(nudged)  # still "close", just not exact
    assert not is_exact_identity_rot_matrix(Placement(xdir=(0, 1, 0), zdir=(0, 0, 1)).rot_matrix)


def test_identity_shortcut_in_transform_matches_the_explicit_inverse():
    """The skipped ``inv(I)`` / ``@ I`` must be bit-for-bit no-ops."""
    rng = np.random.default_rng(13)
    identity_place = Placement()
    for _ in range(500):
        axis = rng.normal(size=3)
        axis = axis / np.linalg.norm(axis)
        place = Placement.from_axis_angle(axis.tolist(), float(rng.uniform(-360, 360)), origin=rng.normal(size=3))
        arr = rng.normal(size=(4, 3)) * 10.0 ** rng.integers(-6, 6)

        explicit = (arr - identity_place.origin) @ (
            place.rot_matrix @ np.linalg.inv(identity_place.rot_matrix)
        ).T + place.origin
        assert place.transform_array_from_other_place(arr, identity_place).tobytes() == explicit.tobytes()

        explicit_no_translation = arr @ (place.rot_matrix @ np.linalg.inv(identity_place.rot_matrix)).T
        assert (
            place.transform_array_from_other_place(arr, identity_place, ignore_translation=True).tobytes()
            == explicit_no_translation.tobytes()
        )


def test_rot_matrix_inv_matches_a_direct_inverse():
    rng = np.random.default_rng(14)
    for _ in range(200):
        axis = rng.normal(size=3)
        axis = axis / np.linalg.norm(axis)
        place = Placement.from_axis_angle(axis.tolist(), float(rng.uniform(-360, 360)))
        assert place.rot_matrix_inv.tobytes() == np.linalg.inv(place.rot_matrix).tobytes()


def test_local_identity_detection():
    assert Placement()._is_local_identity()
    assert Placement(origin=(0, 0, 0))._is_local_identity()
    assert Placement(origin=(0, 0, 0), xdir=(1, 0, 0), ydir=(0, 1, 0), zdir=(0, 0, 1))._is_local_identity()
    assert Placement(origin=(-0.0, 0.0, -0.0))._is_local_identity()
    assert not Placement(origin=(5e-324, 0, 0))._is_local_identity()
    assert not Placement(origin=(1, 0, 0))._is_local_identity()
    assert not Placement(xdir=(0, 1, 0), zdir=(0, 0, 1))._is_local_identity()
    assert not Placement(origin=Direction(0, 0))._is_local_identity()  # 2D origin
