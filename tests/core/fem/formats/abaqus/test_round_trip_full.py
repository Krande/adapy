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
    "amplitudes": (AssertionError, "section int_points (b); section names (c)"),
    "boundary_conditions": (
        AssertionError,
        "connector placement: part-level connector reads back at assembly level; section names (c)",
    ),
    "connectors": (
        AssertionError,
        "connector placement: part-level connectors, sets and csys read back at assembly level",
    ),
    "constraints": (AssertionError, "coupling dofs/sets as surfaces (b); MPC and tie tolerance (b); section names (c)"),
    "constraints_assembly_level": (AssertionError, "assembly-level coupling dofs/sets (b); section names (c)"),
    "constraints_equation": (NotImplementedError, "*Equation (c)"),
    "elements_line_explicit": (AssertionError, "section names (c)"),
    "elements_line_profiles": (AssertionError, "beam section RECT (b); beam general section (a); section names (c)"),
    "elements_line_second_order": (AssertionError, "section names (c)"),
    "elements_line_verbatim": (AssertionError, "section names (c); verbatim beam section (d)"),
    "elements_shell": (AssertionError, "section int_points (b); section names (c)"),
    "elements_shell_second_order": (AssertionError, "section int_points (b); section names (c)"),
    "elements_shell_tri6": (AssertionError, "section int_points (b); section names (c)"),
    "elements_shell_tri7": (AssertionError, "node coordinates (c); section int_points (b); section names (c)"),
    "initial_conditions": (AssertionError, "initial conditions (e); section int_points (b); section names (c)"),
    "interactions": (AssertionError, "interactions (e); section int_points (b); section names (c)"),
    "loads": (AssertionError, "section int_points (b); section names (c)"),
    "masses": (AssertionError, "mass elements: read back as MASS element plus Mass (b); section names (c)"),
    "masses_anisotropic": (NotImplementedError, "anisotropic mass (c)"),
    "materials": (
        AssertionError,
        "material density (c); material damping (a); material expansion (b); material no-compression (a); material plasticity (c)",
    ),
    "multi_part": (AssertionError, "multi-part (c); node coordinates (c)"),
    "outputs": (
        AssertionError,
        "connector placement; the connector elset a history output names is not written (c); section names (c)",
    ),
    "read_back_deck": (AssertionError, "node coordinates (c)"),
    "reference_point": (AttributeError, "reference points (c)"),
    "sections_zero_thickness": (AssertionError, "zero-thickness section (c)"),
    "sets": (AssertionError, "section int_points (b); section names (c)"),
    "sets_empty": (AssertionError, "section int_points (b); section names (c); empty set (c)"),
    "springs": (AssertionError, "springs (a); section int_points (b); section names (c)"),
    "springs_two_node": (ValueError, "SPRING2 (c)"),
    "steps_complex_eigen": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_dynamic_implicit": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_eigen": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_explicit": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_raw_input": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_static": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "steps_steady_state": (
        AssertionError,
        "section int_points (b); section names (c)",
    ),
    "surfaces": (AssertionError, "section int_points (b); section names (c); node surface weight (c)"),
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
