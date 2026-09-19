"""The ``examples/fem/sections_and_offsets.py`` deck, without Sestra.

The example exists to be solved and looked at, which no test can do. What a test can
do is guarantee the thing the reviewer is about to trust: that the deck the example
writes still carries the offsets the table in its docstring promises, for every
section type. Sestra itself is not run here -- it is not installed in CI.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

import ada
from ada.geom.direction import Direction

ROOT_DIR = pathlib.Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def example():
    path = ROOT_DIR / "examples" / "fem" / "sections_and_offsets.py"
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
    # Two elements per beam, so two GELREF1 records each.
    assert records["GELREF1"] == 2 * len(expected)

    # Five distinct offset vectors across the four variants, and the records are
    # deduplicated, so all eight rows share those five: b contributes one (its two
    # ends share a vector), c and d two each, a none.
    distinct = {e for up_e1_e2 in example.VARIANTS.values() for e in up_e1_e2[1:] if e is not None}
    assert len(distinct) == 5
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
