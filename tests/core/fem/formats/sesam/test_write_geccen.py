"""Beam end eccentricities survive a write -> read round trip through a Sesam deck.

The reader has understood ``GECCEN`` for a long time; the writer never emitted one, so
any model with offset beams came back out of a Sesam deck with every beam sitting on
its own axis. These tests pin both halves of that: the records land in the deck, and
the ``e1``/``e2`` a beam was built with are the ones that come back.

The sign convention is what they are really guarding. A ``GECCEN`` vector is a global
offset to be ADDED to the node position; ``Beam.e1``/``e2`` are its negation, because
the geometry path starts its offsets from ``-e``. Two negations that have to cancel
exactly is the kind of thing that reads fine and is wrong, so it is measured here
rather than argued.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

import ada
from ada.config import Config
from ada.fem.formats.sesam.read import cards
from ada.geom.direction import Direction

# name -> (e1, e2). One beam each, one element each, so both ends land on the same
# element and the "shared vector" and "per-node tail" GELREF1 forms are both exercised.
CASES = {
    "E1ONLY": ((0.0, 0.0, -0.3), None),
    "E2ONLY": (None, (0.0, 0.25, 0.0)),
    "BOTHEQ": ((0.0, 0.0, -0.3), (0.0, 0.0, -0.3)),
    "UNEQUAL": ((0.0, 0.2, 0.1), (0.0, -0.2, 0.1)),
    "AXIAL": ((0.0, -0.5, 0.05), (0.0, 0.0, 0.05)),
    "NOECC": (None, None),
}


def _write_deck(tmp_path, cases=None):
    """Build one 4 m beam per case, mesh it as a single line element, write the deck."""
    cases = CASES if cases is None else cases
    beams = [
        ada.Beam(name, (0, i * 2.0, 0), (4, i * 2.0, 0), "IPE300", e1=e1, e2=e2)
        for i, (name, (e1, e2)) in enumerate(cases.items())
    ]
    p = ada.Part("p") / beams
    a = ada.Assembly("a") / p
    # A mesh size longer than the beam keeps it at one element, so e1 and e2 are two
    # ends of the same element rather than of two.
    p.fem = p.to_fem_obj(5.0, "line")
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    return tmp_path / "m" / "mT1.FEM"


def _geccen_vectors(deck_text) -> dict[int, tuple[float, float, float]]:
    out = {}
    for m in cards.re_geccen.finditer(deck_text):
        d = m.groupdict()
        out[int(float(d["eccno"]))] = (float(d["ex"]), float(d["ey"]), float(d["ez"]))
    return out


def _gelref1_eccno_fields(deck_text) -> list[int]:
    """The ``eccno`` field of every GELREF1 record in the deck."""
    return [int(float(m.groupdict()["eccno"])) for m in cards.GELREF1.to_ff_re().finditer(deck_text)]


def _beams_by_case(assembly) -> dict[str, ada.Beam]:
    """Read-back beams keyed by the case they came from, via their y coordinate.

    ``from_fem`` names the concepts after the element ids, not after the original
    beams, so the row a beam sits in is what identifies it.
    """
    by_case = {}
    for bm in assembly.get_all_physical_objects(by_type=ada.Beam):
        row = int(round(float(bm.n1.p[1]) / 2.0))
        by_case[list(CASES)[row]] = bm
    return by_case


def test_geccen_written_and_referenced(tmp_path):
    deck = _write_deck(tmp_path)
    text = deck.read_text()

    vectors = _geccen_vectors(text)
    # Five distinct non-zero offsets across the six beams: BOTHEQ's two ends share one,
    # and NOECC has none. Deduplication is the point -- a real deck repeats the same
    # handful of offsets over thousands of stiffeners.
    expected = {tuple(-np.array(e, dtype=float)) for _, ends in CASES.items() for e in ends if e is not None}
    assert len(vectors) == len(expected)
    assert {tuple(round(c, 10) for c in v) for v in vectors.values()} == {
        tuple(round(c, 10) for c in v) for v in expected
    }

    eccnos = _gelref1_eccno_fields(text)
    assert len(eccnos) == len(CASES)
    # BOTHEQ is the only beam whose two ends share a vector, so it is the only one
    # written with a single positive eccno; the four mixed ones use the -1 tail form.
    assert len([v for v in eccnos if v > 0]) == 1
    assert eccnos.count(-1) == 4
    assert eccnos.count(0) == 1  # NOECC gets no eccentricity number at all


def test_geccen_roundtrips_e1_e2(tmp_path):
    deck = _write_deck(tmp_path)
    b = ada.from_fem(deck, create_concept_objects=True)
    by_case = _beams_by_case(b)

    assert len(by_case) == len(CASES)
    for name, (e1, e2) in CASES.items():
        bm = by_case[name]
        for expected, actual, end in ((e1, bm.e1, "e1"), (e2, bm.e2, "e2")):
            if expected is None:
                assert actual is None, f"{name}.{end} came back as {actual}"
            else:
                assert actual is not None, f"{name}.{end} was dropped"
                assert Direction(*expected).is_equal(Direction(*actual)), f"{name}.{end}: {actual} != {expected}"


def test_beams_without_offsets_get_no_eccentricity(tmp_path):
    """A model with no offsets at all writes no GECCEN records and no eccno fields."""
    deck = _write_deck(tmp_path, cases={"PLAIN": (None, None), "ALSOPLAIN": (None, None)})
    text = deck.read_text()
    assert "GECCEN" not in text
    assert _gelref1_eccno_fields(text) == [0, 0]


def test_fem2concepts_include_ecc_is_respected(tmp_path):
    """The read side's opt-out still applies: the records are in the deck, the
    concepts just don't pick them up."""
    deck = _write_deck(tmp_path)
    assert "GECCEN" in deck.read_text()

    # ``update_config_globally`` sets an environment variable and ``reload_config``
    # re-reads the environment, so putting the value back means REMOVING the variable,
    # not reloading over it. Left in place it silently switches eccentricities off for
    # every test that runs after this one.
    env_key = "ADA_FEM_CONVERT_OPTIONS_FEM2CONCEPTS_INCLUDE_ECC"
    Config().update_config_globally("fem_convert_options_fem2concepts_include_ecc", False)
    try:
        b = ada.from_fem(deck, create_concept_objects=True)
        for bm in b.get_all_physical_objects(by_type=ada.Beam):
            assert bm.e1 is None and bm.e2 is None
    finally:
        Config().update_config_globally("fem_convert_options_fem2concepts_include_ecc", True)
        os.environ.pop(env_key, None)

    assert Config().fem_convert_options_fem2concepts_include_ecc is True


@pytest.mark.parametrize("mesh_size", [5.0, 2.0])
def test_offsets_land_on_the_end_elements_only(tmp_path, mesh_size):
    """Subdividing a beam must not spread its end offsets over the interior nodes.

    At mesh size 2.0 the 4 m beam becomes two elements; the offsets belong to the
    outermost nodes only, so the middle node stays on the nodal line.
    """
    bm = ada.Beam("B", (0, 0, 0), (4, 0, 0), "IPE300", e1=(0, 0, -0.3), e2=(0, 0.2, 0.1))
    p = ada.Part("p") / bm
    ada.Assembly("a") / p
    p.fem = p.to_fem_obj(mesh_size, "line")

    ecc_by_node: dict[int, np.ndarray] = {}
    for el in p.fem.elements.lines_ecc:
        for end in (el.eccentricity.end1, el.eccentricity.end2):
            if end is not None:
                ecc_by_node[end.node.id] = end.ecc_vector

    assert len(ecc_by_node) == 2
    offset_x = sorted(p.fem.nodes.from_id(nid).p[0] for nid in ecc_by_node)
    assert offset_x == [0.0, 4.0]
