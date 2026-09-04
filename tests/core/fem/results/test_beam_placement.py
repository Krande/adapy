"""Profile-centroid placement for eccentric beams.

The arithmetic that decides where a stiffener lands. Its failure mode is silent —
a wrong sign draws every beam in the model on the far side of its plate and
nothing raises — so the pieces that can be tested without OCC are tested here.
"""

import numpy as np

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


def test_beam_offsets_are_applied_negated_by_the_geometry_path():
    """The convention the bake's sign depends on.

    ``BeamJustification.curve_offset_local`` documents "local offsets start from
    -e" and implements ``off = -e``, so a geometric translation must be handed to
    ``Beam.e1`` with its sign flipped. Getting this wrong is close to invisible:
    a section symmetric in EXTENT still lands a face on its plate, so a
    flush check passes while the profile is mirrored — an unsymmetric T-girder
    ends up with its wide flange welded to the plate and its web tip in the air.

    Pinned here because it is someone else's convention, in another module, that
    our arithmetic silently rides on.
    """
    from ada import Beam

    def up_component(value: float) -> float:
        bm = Beam("ecc_sign", (0, 0, 0), (1, 0, 0), sec="TG600x300x20x32", up=(0, 0, 1))
        bm.e1 = (0.0, 0.0, value)
        bm.e2 = (0.0, 0.0, value)
        return float(bm.offset_helper.curve_offset_local().end1[2])

    # Differenced, because the same call also folds in the section's own centroid
    # correction. That constant cancels; the response to e is what is pinned here.
    delta = up_component(0.25) - up_component(-0.25)
    assert np.isclose(delta, -0.5, atol=1e-9), (
        f"expected the offset to move OPPOSITE to e (delta -0.5), got {delta}"
    )


def test_line_elem_to_beam_hands_over_a_negated_eccentricity():
    """The conversion path's sign, which was half-right for years.

    Beam.e1/e2 are applied as ``-e`` by the geometry path, so an eccentricity —
    a global offset from the node to the beam end — has to be handed over
    negated. It used to negate only y and z, which cancels that convention on
    those two axes and leaves x inverted: the vector actually applied came back
    (-ex, +ey, +ez), so a beam offset along x was placed on the wrong side of its
    plate and one offset along y or z was fine. That asymmetry is why it survived.
    """
    from ada import Beam, Node, Part, Section
    from ada.fem import Elem, FemSection, FemSet
    from ada.fem.elements import EccPoint, Eccentricity
    from ada.fem.formats.utils import line_elem_to_beam
    from ada.fem.shapes.definitions import LineShapes
    from ada.materials import Material

    part = Part("ecc_sign")
    n1 = Node((0.0, 0.0, 0.0), 1)
    n2 = Node((1.0, 0.0, 0.0), 2)
    el = Elem(1, [n1, n2], LineShapes.LINE)
    el.fem_sec = FemSection(
        "fs", "line", FemSet("s", [el]), Material("mat"), Section("IPE300", from_str="IPE300"), local_z=(0, 0, 1)
    )

    ecc = np.array([0.3, -0.4, 0.5])
    el.eccentricity = Eccentricity(EccPoint(n1, ecc), EccPoint(n2, ecc))

    bm = line_elem_to_beam(el, part, "BM")
    assert isinstance(bm, Beam)
    # Every component negated, not just y and z.
    assert np.allclose(np.asarray(bm.e1, dtype=float), -ecc, atol=1e-12), (
        f"expected {(-ecc).tolist()}, got {np.asarray(bm.e1, dtype=float).tolist()}"
    )
    assert np.allclose(np.asarray(bm.e2, dtype=float), -ecc, atol=1e-12)
