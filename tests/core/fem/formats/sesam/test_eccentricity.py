"""GELREF1 tail decoding for beam eccentricities.

The tail is positional and its layout depends on which OPT fields are -1, so the
risk here is reading a fixation number as an eccentricity number and offsetting a
beam by whatever that index happens to point at. These cases pin the ordering.
"""

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.results.eccentricity import (
    element_eccentricities,
    geccen_vectors,
)

ELNO, GEONO, FIXNO, ECCNO, TRANSNO = cards.GELREF1.get_indices_from_names(
    ["elno", "geono", "fixno", "eccno", "transno"]
)
HEADER_LEN = TRANSNO + 1

GECCEN = [
    [1, 0.1, 0.0, 0.0],
    [2, 0.0, 0.2, 0.0],
    [3, 0.0, 0.0, -0.3],
]


def _row(elno, *, geono=1, fixno=0, eccno=0, transno=1, tail=()):
    row = [0.0] * HEADER_LEN
    row[ELNO] = elno
    row[GEONO] = geono
    row[FIXNO] = fixno
    row[ECCNO] = eccno
    row[TRANSNO] = transno
    return row + list(tail)


def test_geccen_vectors_are_keyed_by_eccno():
    vecs = geccen_vectors(GECCEN)
    assert set(vecs) == {1, 2, 3}
    assert np.allclose(vecs[3], [0.0, 0.0, -0.3])


def test_constant_eccentricity_applies_to_every_node():
    out = element_eccentricities([_row(10, eccno=2)], GECCEN, {10: 2})
    assert np.allclose(out[10][0], [0.0, 0.2, 0.0])
    assert np.allclose(out[10][1], [0.0, 0.2, 0.0])


def test_per_node_list_is_read_from_the_tail():
    out = element_eccentricities([_row(11, eccno=-1, tail=(1, 3))], GECCEN, {11: 2})
    assert np.allclose(out[11][0], [0.1, 0.0, 0.0])
    assert np.allclose(out[11][1], [0.0, 0.0, -0.3])


def test_a_fixno_list_is_skipped_before_the_eccno_list():
    # fixno == -1 puts NNOD entries in front of the eccentricity list. Reading
    # from the wrong offset here picked up release numbers (7, 8) and offset the
    # beam by whichever GECCEN record they collided with.
    row = _row(12, fixno=-1, eccno=-1, tail=(7, 8, 2, 2))
    out = element_eccentricities([row], GECCEN, {12: 2})
    assert np.allclose(out[12][0], [0.0, 0.2, 0.0])
    assert np.allclose(out[12][1], [0.0, 0.2, 0.0])


def test_a_geono_list_is_skipped_too_and_stacks_with_fixno():
    row = _row(13, geono=-1, fixno=-1, eccno=-1, tail=(5, 5, 7, 8, 1, 3))
    out = element_eccentricities([row], GECCEN, {13: 2})
    assert np.allclose(out[13][0], [0.1, 0.0, 0.0])
    assert np.allclose(out[13][1], [0.0, 0.0, -0.3])


def test_zero_eccno_means_no_offset_rather_than_a_zero_offset():
    out = element_eccentricities([_row(14, eccno=0)], GECCEN, {14: 2})
    assert 14 not in out


def test_a_single_un_offset_end_is_reported_as_none():
    out = element_eccentricities([_row(15, eccno=-1, tail=(0, 1))], GECCEN, {15: 2})
    assert out[15][0] is None
    assert np.allclose(out[15][1], [0.1, 0.0, 0.0])


def test_an_element_with_neither_end_offset_is_absent():
    out = element_eccentricities([_row(16, eccno=-1, tail=(0, 0))], GECCEN, {16: 2})
    assert 16 not in out


def test_a_truncated_tail_is_skipped_not_guessed():
    # Half a list is a decode error. Placing the beam on its axis would hide it.
    out = element_eccentricities([_row(17, eccno=-1, tail=(1,))], GECCEN, {17: 2})
    assert 17 not in out


def test_no_geccen_records_means_no_eccentricities():
    assert element_eccentricities([_row(18, eccno=2)], [], {18: 2}) == {}


def test_four_node_shell_carries_one_vector_per_node():
    row = _row(19, eccno=-1, tail=(1, 2, 3, 1))
    out = element_eccentricities([row], GECCEN, {19: 4})
    assert len(out[19]) == 4
    assert np.allclose(out[19][3], [0.1, 0.0, 0.0])
