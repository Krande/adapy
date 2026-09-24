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
    "amplitudes": (AssertionError, "amplitudes (a)"),
    "boundary_conditions": (AssertionError, "connector placement: part-level connector reads back at assembly level"),
    "connectors": (
        AssertionError,
        "connector placement: part-level connectors, sets and csys read back at assembly level",
    ),
    "constraints": (AssertionError, "coupling dofs/sets as surfaces (b); MPC and tie tolerance (b)"),
    "constraints_assembly_level": (AssertionError, "assembly-level coupling dofs/sets (b)"),
    "constraints_equation": (NotImplementedError, "*Equation (c)"),
    "elements_line_explicit": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "initial_conditions": (AssertionError, "initial conditions (e)"),
    "interactions": (AssertionError, "interactions (e)"),
    "loads": (AssertionError, "amplitudes (a); load csys (*Transform) (a); steps (+ their loads, BCs, outputs) (a)"),
    "masses": (AssertionError, "mass elements: read back as MASS element plus Mass (b)"),
    "masses_anisotropic": (NotImplementedError, "anisotropic mass (c)"),
    "multi_part": (AssertionError, "multi-part (c)"),
    "outputs": (AssertionError, "steps and outputs (a); connector placement"),
    "reference_point": (AttributeError, "reference points (c)"),
    "sections_zero_thickness": (
        AssertionError,
        "a zero-thickness shell section has no Abaqus form (d): left out, reported as omitted",
    ),
    "sets_empty": (AssertionError, "empty set (c)"),
    "springs": (AssertionError, "springs (a)"),
    "springs_two_node": (ValueError, "SPRING2 (c)"),
    "steps_complex_eigen": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "steps_dynamic_implicit": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "steps_eigen": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "steps_explicit": (AssertionError, "interactions (e); steps (+ their loads, BCs, outputs) (a)"),
    "steps_raw_input": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "steps_static": (AssertionError, "step-level BC (b); steps (+ their loads, BCs, outputs) (a)"),
    "steps_steady_state": (AssertionError, "steps (+ their loads, BCs, outputs) (a)"),
    "surfaces": (AssertionError, "node surface weight (c)"),
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
