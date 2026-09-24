"""The whole model round-trips: ``canonical(read(write(model))) == canonical(model)``.

``test_round_trip.py`` checks structure (counts, names) and that a second pass changes nothing.
This checks everything the Abaqus writer can express, original against read-back, through the
paths a user takes (``Assembly.to_fem`` / ``ada.from_fem``). What "the same" means, and every
representation-only difference it forgives, is written down in ``canonical.py`` (rules R1-R10).

Target: 100% -- every zoo model round-trips whole. ``FULL_ROUND_TRIP_GAPS`` is the ledger of what
does not yet, one entry per model, naming each construct that differs and its cause:

    (a) the reader does not read the keyword      (b) the reader reads it wrongly
    (c) the writer writes it wrongly or lossily   (d) the adapy model cannot hold it
    (e) read, then dropped by ``ada.from_fem``'s hand-over (``FEM.__add__``)

Every entry is ``xfail(strict=True)``: fixing a gap makes its case fail until the entry is removed,
so the ledger can only shrink. A model whose write or read raises shows only that error; the gaps
behind it appear once it is fixed.
"""

from __future__ import annotations

import pathlib

import pytest

import ada

from .canonical import canonical, diff_paths
from .zoo import ZOO

FULL_ROUND_TRIP_GAPS = {
    "boundary_conditions": (AssertionError, "connector-motion BCs are not read; a BC's amplitude is not read"),
    "interactions": (AssertionError, "a raw interaction (aba_bulk text) does not read back"),
    "outputs": (AssertionError, "the connector elset a history output names is not written"),
    "sections_zero_thickness": (
        AssertionError,
        "a zero-thickness shell section has no Abaqus form: left out, reported as omitted",
    ),
}


def _cases():
    for name in sorted(ZOO):
        gap = FULL_ROUND_TRIP_GAPS.get(name)
        marks = [pytest.mark.xfail(raises=gap[0], strict=True, reason=gap[1])] if gap else []
        yield pytest.param(name, marks=marks, id=name)


def _write_read(model: ada.Assembly, tmp_path: pathlib.Path) -> ada.Assembly:
    model.to_fem("rt", fem_format="abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    return ada.from_fem(next(tmp_path.rglob("rt.inp")), "abaqus")


@pytest.mark.parametrize("name", _cases())
def test_every_zoo_model_round_trips_whole(name, tmp_path):
    model = ZOO[name]()
    original = canonical(model)  # before writing: to_fem may reorganise the model it is given
    read_back = canonical(_write_read(model, tmp_path))
    diffs = diff_paths(original, read_back)
    assert not diffs, f"{len(diffs)} difference(s), original != read-back:\n  " + "\n  ".join(diffs[:80])


def test_canonical_is_deterministic():
    """Two builds of the same model describe identically, or every comparison above is noise."""
    for name, build in ZOO.items():
        try:
            assert canonical(build()) == canonical(build()), name
        except (NotImplementedError, AttributeError, ValueError):
            continue  # a model canonical() cannot describe fails in its own case above
