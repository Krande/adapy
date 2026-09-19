"""The ``examples/fem/sections_and_offsets.py`` deck, without Sestra.

The example exists to be solved and looked at, which no test can do. What a test can
do is guarantee the thing the reviewer is about to trust: that the deck the example
writes still carries the offsets the table in its docstring promises, for every
section type. Sestra itself is not run here -- it is not installed in CI.
"""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pytest

import ada
from ada.geom.direction import Direction

ROOT_DIR = pathlib.Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def example():
    path = ROOT_DIR / "examples" / "fem" / "sections_and_offsets.py"
    if not path.is_file():
        # The conda feedstock's test job ships tests/ without examples/; the
        # deck is then simply not here to build, the same as a missing fixture.
        pytest.skip(f"example not present: {path}")
    spec = importlib.util.spec_from_file_location("sections_and_offsets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def deck(example, tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("sections_and_offsets")
    example.main(out_dir, execute=False)
    return out_dir / "sections_and_offsets" / "sections_and_offsetsT1.FEM"


def test_deck_has_one_section_per_type_and_the_expected_offsets(example, deck):
    assert deck.exists()
    expected = example.expected_offsets()
    assert len(expected) == len(example.SECTIONS) * len(example.VARIANTS)

    records = _record_counts(deck.read_text())
    # One TDSECT per section type. Two types collapsing into one -- a section name
    # colliding, say -- would show up as a shortfall here.
    assert records["TDSECT"] == len(example.SECTIONS)
    # Two elements per beam, so two GELREF1 records each, plus the viewer plate's
    # shells (see the example's docstring for why a beams-only deck will not do).
    assert records["GELREF1"] >= 2 * len(expected)

    # Seven distinct offset vectors across the four variants, and the records are
    # deduplicated, so all eight rows share those seven: b contributes one (its two
    # ends and its midspan share a vector), c and d three each (each end, and the
    # interpolated midspan between them), a none.
    distinct = set()
    for _up, e1, e2 in example.VARIANTS.values():
        if e1 is None and e2 is None:
            continue
        a = np.zeros(3) if e1 is None else np.asarray(e1, float)
        b = np.zeros(3) if e2 is None else np.asarray(e2, float)
        for vec in (a, b, 0.5 * (a + b)):
            if np.any(vec):
                distinct.add(tuple(np.round(vec, 10)))
    assert len(distinct) == 7
    assert records["GECCEN"] == len(distinct)


def _record_counts(text: str) -> dict[str, int]:
    """Count Sesam records by flag. A record's flag only ever starts a line."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if line[:1].isalpha():
            counts[line.split()[0]] = counts.get(line.split()[0], 0) + 1
    return counts


def test_offsets_survive_into_the_deck(example, deck):
    """Read the deck back as concepts and compare every beam's e1/e2 with the table.

    The example meshes each beam into two elements, so the deck's concepts come back
    as two beams per original: the one starting at x=0 carries ``e1``, the one ending
    at the far end carries ``e2``.
    """
    b = ada.from_fem(deck, create_concept_objects=True)

    found: dict[str, list[tuple | None]] = {}
    for bm in b.get_all_physical_objects(by_type=ada.Beam):
        name = example.beam_name_at(bm.n1.p)
        ends = found.setdefault(name, [None, None])
        if abs(float(bm.n1.p[0])) < 1e-9:
            ends[0] = bm.e1
        if abs(float(bm.n2.p[0]) - example.LENGTH) < 1e-9:
            ends[1] = bm.e2

    expected = example.expected_offsets()
    assert set(found) == set(expected)

    for name, (e1, e2) in expected.items():
        for want, got, end in ((e1, found[name][0], "e1"), (e2, found[name][1], "e2")):
            if want is None:
                assert got is None, f"{name}.{end} came back as {got}, expected no offset"
            else:
                assert got is not None, f"{name}.{end} was dropped from the deck"
                assert Direction(*want).is_equal(Direction(*got)), f"{name}.{end}: {got} != {want}"
