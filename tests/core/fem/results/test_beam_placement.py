"""Profile-centroid placement for eccentric beams.

The arithmetic that decides where a stiffener lands. Its failure mode is silent —
a wrong sign draws every beam in the model on the far side of its plate and
nothing raises — so the pieces that can be tested without OCC are tested here.
"""

import numpy as np
import pytest

from ada.fem.results.beam_placement import (
    SectionCentroidCache,
    _volume_centroid,
    eccentric_shift,
)


def _box(lo, hi):
    """Closed, outward-wound triangulated box."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array(
        [
            [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
        ],
        dtype=float,
    )
    t = np.array(
        [
            [0, 3, 2], [0, 2, 1],   # bottom
            [4, 5, 6], [4, 6, 7],   # top
            [0, 1, 5], [0, 5, 4],
            [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6],
            [3, 0, 4], [3, 4, 7],
        ],
        dtype=np.int64,
    )
    return v, t


def test_volume_centroid_of_a_unit_cube_at_the_origin():
    c = _volume_centroid(*_box((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)))
    assert np.allclose(c, [0, 0, 0], atol=1e-12)


def test_volume_centroid_follows_a_translated_box():
    c = _volume_centroid(*_box((2.0, -1.0, 4.0), (3.0, 1.0, 5.0)))
    assert np.allclose(c, [2.5, 0.0, 4.5], atol=1e-12)


def test_volume_centroid_is_independent_of_winding():
    v, t = _box((0, 0, 0), (1, 2, 3))
    flipped = t[:, ::-1].copy()
    assert np.allclose(_volume_centroid(v, t), _volume_centroid(v, flipped), atol=1e-12)


def test_volume_centroid_of_a_degenerate_solid_is_none():
    v, t = _box((0, 0, 0), (1, 1, 1))
    flat = v.copy()
    flat[:, 2] = 0.0
    assert _volume_centroid(flat, t) is None


class _FixedCache(SectionCentroidCache):
    """A cache with the measurement stubbed, so the shift arithmetic is testable
    without OCC — which is the part that gets the sign wrong."""

    def __init__(self, local):
        super().__init__()
        self._local = local

    def offset_local(self, section):
        return self._local


class _Beam:
    def __init__(self, yvec, up):
        self.section = object()
        self.yvec = yvec
        self.up = up


def test_a_profile_already_on_its_centroid_gets_no_shift():
    # This is what protects the L sections: their builder already places the
    # profile one centroid-distance off the axis, which is where the eccentricity
    # would have put it. Correcting must therefore be a no-op for them.
    beam = _Beam(yvec=(0, 1, 0), up=(0, 0, 1))
    ecc = np.array([0.0, 0.0, 0.135])
    shift = eccentric_shift(beam, ecc, _FixedCache((0.0, 0.135)))
    assert np.allclose(shift, [0, 0, 0], atol=1e-12)


def test_a_centred_profile_is_shifted_by_the_full_eccentricity():
    beam = _Beam(yvec=(0, 1, 0), up=(0, 0, 1))
    ecc = np.array([0.0, 0.0, -0.421])
    shift = eccentric_shift(beam, ecc, _FixedCache((0.0, 0.0)))
    assert np.allclose(shift, [0, 0, -0.421], atol=1e-12)


def test_the_measured_offset_is_re_expressed_onto_the_beams_own_axes():
    # Same section, beam rotated: the correction has to rotate with it, or every
    # beam not aligned with the probe's axes lands somewhere arbitrary.
    beam = _Beam(yvec=(1, 0, 0), up=(0, 1, 0))
    shift = eccentric_shift(beam, np.zeros(3), _FixedCache((0.3, 0.4)))
    assert np.allclose(shift, [-0.3, -0.4, 0.0], atol=1e-12)


def test_an_unmeasurable_section_yields_no_shift_rather_than_a_guess():
    beam = _Beam(yvec=(0, 1, 0), up=(0, 0, 1))
    assert eccentric_shift(beam, np.array([1.0, 0, 0]), _FixedCache(None)) is None
