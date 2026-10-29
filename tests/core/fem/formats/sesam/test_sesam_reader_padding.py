"""Reader robustness for decks SESTRA echoes into a results file (padded records, odd TRANSNO)."""

import numpy as np

from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.read.read_sections import _reference_z


def test_stale_bnbcd_records_are_dropped_from_the_exported_deck():
    from ada.fem.formats.sesam.results.export_fem import _consistent_records

    good = (1198.0, 6.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0)
    stale = (1.0, 1.0, 0.0, 1.0, 778.0, 0.0, 0.0, 6.0, 0.0, 0.0, 0.0, -2.2e-05, 0.0, 0.0, -2.2e-05, 0.0)
    assert list(_consistent_records("BNBCD", iter([good, stale]))) == [good]
    assert list(_consistent_records("GNODE", iter([stale]))) == [stale]


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


def test_padded_guniv_keeps_the_vector_apart_from_the_padding():
    deck = (
        "GUNIVEC   1.00000000E+00  0.00000000E+00  0.00000000E+00  1.00000000E+00\n"
        "            0.00000000E+00  0.00000000E+00  -9.80665016E+00  0.00000000E+00\n"
        "GUNIVEC   2.00000000E+00  1.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "IEND      0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
    )
    found = [m.groupdict() for m in cards.re_guniv_padded.finditer(deck)]
    assert [(float(d["transno"]), float(d["uniz"])) for d in found] == [(1.0, 1.0), (2.0, 0.0)]


def _gelref1(records: str) -> dict:
    return {int(float(m.group("elno"))): m.groupdict() for m in cards.GELREF1.to_ff_re().finditer(records)}


def test_a_transno_of_minus_one_is_read_from_the_per_node_list():
    """TRANSNO -1 is "per node", like ECCNO -1: the trailing list holds the per-node ECCNOs first
    (when ECCNO is -1), then the per-node TRANSNOs. A 3-node beam with ECCNO -1 and TRANSNO -1 is
    oriented by its first node's TRANSNO (here 142), not by a default."""
    from ada.fem.formats.sesam.read.read_sections import element_transno

    deck = (
        "GELREF1   5.00000000E+00  6.30000000E+01  0.00000000E+00  0.00000000E+00\n"
        "            0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "            2.03000000E+02  0.00000000E+00  -1.00000000E+00  -1.00000000E+00\n"
        "            1.59550000E+04  1.59560000E+04  1.59570000E+04  1.42000000E+02\n"
        "            1.42000000E+02  1.42000000E+02\n"
        "GELREF1   6.00000000E+00  6.30000000E+01  0.00000000E+00  0.00000000E+00\n"
        "            0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "            1.55000000E+02  0.00000000E+00  0.00000000E+00  -1.00000000E+00\n"
        "            7.00000000E+00  7.00000000E+00  7.00000000E+00\n"
        "GELREF1   7.00000000E+00  6.30000000E+01  0.00000000E+00  0.00000000E+00\n"
        "            0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
        "            1.55000000E+02  0.00000000E+00  0.00000000E+00  3.00000000E+00\n"
        "IEND      0.00000000E+00  0.00000000E+00  0.00000000E+00  0.00000000E+00\n"
    )
    refs = _gelref1(deck)
    assert element_transno(refs[5], 3) == 142, "after the 3 per-node ECCNOs"
    assert element_transno(refs[6], 3) == 7, "no per-node ECCNOs: the list is the TRANSNOs"
    assert element_transno(refs[7], 3) == 3, "a constant TRANSNO is itself"


def test_reference_z_falls_back_when_guniVec_is_missing_or_parallel():
    x = np.array([1.0, 0.0, 0.0])
    assert tuple(_reference_z({}, -1, x)) == (0.0, 0.0, 1.0)
    assert tuple(_reference_z({16: (1.0, 0.0, 0.0)}, 16, x)) == (0.0, 0.0, 1.0)
    assert tuple(_reference_z({1: (0.0, 1.0, 0.0)}, 1, x)) == (0.0, 1.0, 0.0)
    assert tuple(_reference_z({}, -1, np.array([0.0, 0.0, 1.0]))) == (0.0, 1.0, 0.0)
