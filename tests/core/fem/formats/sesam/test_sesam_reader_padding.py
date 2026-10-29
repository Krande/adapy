"""Reader robustness for decks SESTRA echoes into a results file (padded records, odd TRANSNO)."""

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.read.read_sections import _reference_z


def test_padded_gpipe_and_gbarm_do_not_swallow_the_padding():
    deck = (
        "GPIPE     4.00000000E+00  4.09999996E-01  4.49999988E-01  1.99999996E-02\n"
        "            1.00000000E+00  1.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "GBARM    19.00000000E+00  2.39999995E-01  4.05000001E-02  4.05000001E-02\n"
        "            1.00000000E+00  1.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "IEND      0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
    )
    for regex in (cards.re_gpipe, cards.re_gbarm):
        d = regex.search(deck).groupdict()
        assert float(d["sfy"]) == 1.0 and float(d["sfz"]) == 1.0


def test_reference_z_falls_back_when_guniVec_is_missing_or_parallel():
    x = np.array([1.0, 0.0, 0.0])
    assert tuple(_reference_z({}, -1, x)) == (0.0, 0.0, 1.0)
    assert tuple(_reference_z({16: (1.0, 0.0, 0.0)}, 16, x)) == (0.0, 0.0, 1.0)
    assert tuple(_reference_z({1: (0.0, 1.0, 0.0)}, 1, x)) == (0.0, 1.0, 0.0)
    assert tuple(_reference_z({}, -1, np.array([0.0, 0.0, 1.0]))) == (0.0, 1.0, 0.0)
