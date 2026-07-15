"""A plate faces the way its XML says it does, not the way its points wind.

A ``flat_plate`` states its normal outright as a ``<vector>``. The reader used
to ignore it and let ``Plate.from_3d_points`` derive one from the point order —
and the two disagree about half the time, because the points come off the SAT
face's loop, whose winding is its own business. The plate then went back out
facing the other way, which Genie draws inside-out.
"""

import numpy as np
import pytest

from ada.cadit.gxml.read.helpers import _plate_from_3d_points

# a unit square wound counter-clockwise about +z
SQUARE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]


def _normal(points, desired):
    return np.asarray(_plate_from_3d_points("pl", points, 0.01, desired).poly.normal, dtype=float)


class TestOrientation:
    @pytest.mark.parametrize("desired", [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)])
    def test_the_plate_faces_the_way_it_was_asked_to(self, desired):
        assert np.dot(_normal(SQUARE, desired), desired) > 0

    @pytest.mark.parametrize("desired", [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)])
    def test_the_point_winding_does_not_decide_it(self, desired):
        """Both windings of the same square must land on the same normal."""
        forward = _normal(SQUARE, desired)
        backward = _normal(list(reversed(SQUARE)), desired)
        assert np.dot(forward, backward) > 0
        assert np.dot(forward, desired) > 0

    def test_no_normal_asked_for_leaves_it_alone(self):
        """Without a <vector> there is nothing to honour; keep the old behaviour."""
        from ada import Plate

        expected = Plate.from_3d_points("pl", SQUARE, 0.01).poly.normal
        got = _plate_from_3d_points("pl", SQUARE, 0.01, None).poly.normal
        assert np.allclose(np.asarray(got, dtype=float), np.asarray(expected, dtype=float))

    def test_a_normal_it_already_agrees_with_is_not_flipped(self):
        from ada import Plate

        natural = np.asarray(Plate.from_3d_points("pl", SQUARE, 0.01).poly.normal, dtype=float)
        assert np.allclose(_normal(SQUARE, tuple(natural)), natural)

    def test_an_off_axis_normal_still_decides(self):
        """The test only needs the sign of the dot product, not an exact match."""
        assert _normal(SQUARE, (0.1, 0.2, -0.97))[2] < 0
        assert _normal(SQUARE, (0.1, 0.2, 0.97))[2] > 0
