"""Distance queries must work on whatever ``solid_occ()`` actually returns.

``solid_occ()`` hands back an *active-backend handle* -- an adacpp
``ShapeHandle`` under the default backend, not a pythonocc ``TopoDS_Shape``.
The pythonocc ``BRepExtrema_DistShapeShape`` loaders reject a handle with a bare
``TypeError: ... argument 2 of type 'TopoDS_Shape const &'``, so anything that
composes the two is a hard failure on the default backend rather than a slow
path.

Two separate things are pinned here:

* ``ada.cad.minimal_distance_between_shapes`` -- the shape-level primitive for
  callers holding geometry rather than ``Plate`` objects. ``ada.occ.utils`` has
  no backend-neutral equivalent.
* ``are_plates_touching`` -- which no longer computes anything itself. It hit the
  TypeError above because it had been left behind when the rest of this module
  moved to the backend, so it now delegates to ``plates_min_distance``, the
  already-migrated path that answers the same question. These assert the two
  agree, since a silently diverging duplicate is what caused the bug.
"""

import ada
from ada.cad import minimal_distance_between_shapes
from ada.core.clash_check import are_plates_touching
from ada.occ.occ_clash_check import plates_min_distance

_SQUARE = [(0, 0), (1, 0), (1, 1), (0, 1)]


def _plate(name: str, z: float = 0.0) -> ada.Plate:
    return ada.Plate(name, _SQUARE, 0.01, orientation=ada.Placement((0, 0, z)))


def test_minimal_distance_accepts_what_solid_occ_returns():
    a = ada.PrimBox("a", (0, 0, 0), (1, 1, 1))
    b = ada.PrimBox("b", (2, 0, 0), (3, 1, 1))

    # The gap is 1.0 along x. Passing the handles straight through is the case
    # that used to raise TypeError.
    assert minimal_distance_between_shapes(a.solid_occ(), b.solid_occ()) == 1.0


def test_minimal_distance_is_zero_for_overlapping_shapes():
    a = ada.PrimBox("a", (0, 0, 0), (1, 1, 1))
    b = ada.PrimBox("b", (0.5, 0, 0), (1.5, 1, 1))

    assert minimal_distance_between_shapes(a.solid_occ(), b.solid_occ()) == 0.0


def test_are_plates_touching_returns_a_bool_either_way():
    touching = are_plates_touching(_plate("pl1"), _plate("pl2", z=0.005))
    apart = are_plates_touching(_plate("pl3"), _plate("pl4", z=5.0))

    # bool specifically, not a forwarded distance -- coincident plates are at
    # 0.0, which is falsy and would read as "not touching".
    assert touching is True
    assert apart is False


def test_are_plates_touching_agrees_with_the_path_it_delegates_to():
    """The duplicate is gone; this keeps it from growing back."""
    near = (_plate("pl1"), _plate("pl2", z=0.005))
    far = (_plate("pl3"), _plate("pl4", z=5.0))

    for pair in (near, far):
        assert are_plates_touching(*pair) is (plates_min_distance(*pair) is not None)


def test_coincident_plates_count_as_touching():
    """The 0.0-is-falsy case the bool return exists for.

    ``plates_min_distance`` answers 0.0 here, so any implementation that
    forwarded the distance and let a caller test it for truthiness would report
    the most clearly-touching pair there is as apart.
    """
    pl1, pl2 = _plate("pl1"), _plate("pl2")

    assert plates_min_distance(pl1, pl2) == 0.0
    assert are_plates_touching(pl1, pl2) is True
