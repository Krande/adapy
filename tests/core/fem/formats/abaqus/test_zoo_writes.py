"""Every model in the zoo writes, with Abaqus and with Calculix (which shares Abaqus's writers).

Where a writer cannot express a construct, the case is ``xfail(strict=True)`` with the exact
exception it raises: a gap stays visible, and a fix shows up as an unexpected pass rather than
silently.

Abaqus is written twice. ``Assembly.to_fem`` is what users call -- but it merges the model into
ONE part first whenever more than one part has nodes (general.write_to_fem does that for every
format), so the Abaqus writer's own part/instance/assembly-level code is reached only by calling
it directly, which the second test does.
"""

from __future__ import annotations

import pytest

from ada.fem.exceptions import IncompatibleElements
from ada.fem.formats.abaqus.read.lexer import tokenize
from ada.fem.formats.abaqus.write.writer import to_fem as write_abaqus

from .zoo import ZOO

#: The Abaqus writer's own gaps.
ABAQUS_GAPS = {
    "masses_anisotropic": (
        NotImplementedError,
        "ada_to_aba_mass_map is keyed by MassTypes, so Mass.PTYPES.ANISOTROPIC never maps to type=ANISOTROPIC",
    ),
    "constraints_equation": (NotImplementedError, "constraint_str has no branch for Constraint.TYPES.EQUATION"),
    "reference_point": (
        AttributeError,
        "rp_str writes FEM.ref_sets, whose sets have parent=None, and aba_set_str reads parent.options",
    ),
}

#: What the Calculix writer cannot express, beyond the gaps it shares with Abaqus.
CALCULIX_GAPS = {
    "elements_line_profiles": (Exception, "the Calculix beam writer has no FLATBAR profile"),
    "elements_line_explicit": (ValueError, "Calculix has no explicit step"),
    "steps_explicit": (ValueError, "Calculix has no explicit step"),
    "steps_dynamic_implicit": (ValueError, "the Calculix writer has no implicit dynamic step"),
    "loads": (ValueError, "Calculix loads need a fem_set; gravity/acceleration fields have none"),
    "read_back_deck": (IncompatibleElements, "a read deck's elements carry no FemSection, which Calculix needs"),
    "sets_empty": (ValueError, "the Calculix set writer raises on an empty set (Abaqus logs and drops it)"),
}


def _cases(gaps):
    for name in ZOO:
        if name in gaps:
            exc, reason = gaps[name]
            yield pytest.param(name, marks=pytest.mark.xfail(raises=exc, strict=True, reason=reason), id=name)
        else:
            yield pytest.param(name, id=name)


def _write(name: str, fem_format: str, tmp_path):
    a = ZOO[name]()
    a.to_fem(name, fem_format, scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    deck = tmp_path / name / f"{name}.inp"
    assert deck.is_file(), f"{fem_format} wrote no {deck.name}"
    return deck.read_text()


@pytest.mark.parametrize("name", _cases(ABAQUS_GAPS))
def test_the_abaqus_writer_writes_every_zoo_model(name, tmp_path):
    text = _write(name, "abaqus", tmp_path)
    keywords = {b.keyword for b in tokenize(text)}
    # one self-contained deck: a heading, the model, no unresolved *Include left behind
    assert "HEADING" in keywords
    assert "NODE" in keywords
    assert "INCLUDE" not in keywords


@pytest.mark.parametrize("name", _cases(ABAQUS_GAPS))
def test_the_abaqus_writer_called_directly_writes_every_zoo_model(name, tmp_path):
    """No part merge: the writer sees the model's own parts, instances and assembly data."""
    write_abaqus(ZOO[name](), name, tmp_path)
    text = (tmp_path / f"{name}.inp").read_text()
    keywords = {b.keyword for b in tokenize(text)}
    assert {"HEADING", "NODE", "PART", "INSTANCE"} <= keywords
    assert "INCLUDE" not in keywords


@pytest.mark.parametrize("name", _cases(CALCULIX_GAPS))
def test_the_calculix_writer_writes_every_zoo_model_it_supports(name, tmp_path):
    text = _write(name, "calculix", tmp_path)
    assert "NODE" in {b.keyword for b in tokenize(text)}
