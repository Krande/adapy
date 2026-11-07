"""``ada.fem.face_loads``: the consistent nodal weights ``integral(N_i dA)`` of a face, against their closed forms."""

from __future__ import annotations

import numpy as np
import pytest

from ada.fem.face_loads import face_weights

QUAD = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
QUAD8 = QUAD + [(1, 0, 0), (2, 0.5, 0), (1, 1, 0), (0, 0.5, 0)]
TRI = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
TRI6 = TRI + [(0.5, 0, 0), (0.5, 0.5, 0), (0, 0.5, 0)]


@pytest.mark.parametrize(
    "face, want",
    [
        (QUAD, [0.5] * 4),  # a quarter of 2 m2 each
        (QUAD8, [-2 / 12] * 4 + [2 / 3] * 4),  # -A/12 at the corners, A/3 at the mid-sides
        (TRI, [1 / 6] * 3),  # a third of 0.5 m2
        (TRI6, [0.0] * 3 + [1 / 6] * 3),  # nothing at the corners, A/3 at the mid-sides
    ],
    ids=["quad4", "quad8", "tri3", "tri6"],
)
def test_the_weights_of_a_flat_face_are_its_closed_form_shares(face, want):
    assert face_weights(face) == pytest.approx(want, abs=1e-12)


def test_the_weights_of_a_skewed_tilted_quad_sum_to_its_area():
    pts = np.array([(0, 0, 0), (2, 0, 0), (2.5, 1, 0), (0.3, 1.2, 0)], dtype=float)
    turn = np.array([[1, 0, 0], [0, np.cos(0.7), -np.sin(0.7)], [0, np.sin(0.7), np.cos(0.7)]])
    area = 0.5 * np.linalg.norm(np.cross(pts[2] - pts[0], pts[3] - pts[1]))
    assert face_weights(pts @ turn.T).sum() == pytest.approx(area, rel=1e-12)
