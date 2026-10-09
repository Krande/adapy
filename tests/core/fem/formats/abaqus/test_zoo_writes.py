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
from ada.fem.exceptions.model_definition import UnsupportedLoadType
from ada.fem.formats.abaqus.read.lexer import tokenize
from ada.fem.formats.abaqus.write.writer import to_fem as write_abaqus

from .zoo import ZOO

#: The Abaqus writer's own gaps.
ABAQUS_GAPS = {}

#: What the Calculix writer cannot express, beyond the gaps it shares with Abaqus.
CALCULIX_GAPS = {
    # A two-node beam of any section is a U1 general section now, so the FLATBAR profile writes; a three-node beam
    # of an I section has no CalculiX element (U1 has two nodes, B32R takes a geometric outline only).
    "elements_line_second_order": (IncompatibleElements, "a three-node I beam has no CalculiX element"),
    # An angle's y and z are not principal axes (L100x100x10: Iyz = -1.07e-6 m^4); U1 takes principal axes only.
    "elements_line_profiles": (IncompatibleElements, "a U1 general section refuses an angle's product of inertia"),
    "elements_line_explicit": (ValueError, "Calculix has no explicit step"),
    "steps_explicit": (ValueError, "Calculix has no explicit step"),
    "steps_dynamic_implicit": (ValueError, "the Calculix writer has no implicit dynamic step"),
    # Every step is written now, so the second step of these is reached rather than dropped without a word.
    "steps_steady_state": (ValueError, "the Calculix writer has no steady-state dynamics step"),
    "loads": (UnsupportedLoadType, "a pressure on shell face 0 names neither SPOS nor SNEG"),
    "read_back_deck": (IncompatibleElements, "a read deck's elements carry no FemSection, which Calculix needs"),
    "sets_empty": (ValueError, "the Calculix set writer raises on an empty set (Abaqus logs and drops it)"),
    # The Calculix writer used to drop every constraint; it now writes kinematic couplings and refuses the rest
    # rather than describing a different model.
    "constraints": (IncompatibleElements, "the Calculix writer has no *Tie, and no coupling on shell nodes"),
    "constraints_equation": (IncompatibleElements, "the Calculix writer has no *Equation"),
    "constraints_assembly_level": (IncompatibleElements, "a coupling the Calculix writer cannot take"),
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


def test_the_calculix_writer_writes_every_line_profile_but_the_angle(tmp_path):
    """``elements_line_profiles`` stops at its angle; the profiles after it are written here."""
    from .zoo import _line, _model

    a, p, mat = _model()
    names = []
    for i, (name, profile) in enumerate(
        [
            ("ipe", "IPE300"),
            ("box", "BG200x150x6x6"),
            ("pipe", "OD200x10"),
            ("circ", "CIRC100"),
            ("flat", "FB100x10"),
            ("channel", "UNP200"),
        ]
    ):
        _line(p.fem, mat, profile, node_start=10 * i + 1, el_start=10 * i + 1, y=float(i), name=name)
        names.append(name)
    a.to_fem("profiles", "calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
    text = (tmp_path / "profiles" / "profiles.inp").read_text().lower()
    for name in names:
        assert f"*beam section, elset={name}," in text, name


def test_the_calculix_writer_refuses_an_angle_by_its_product_of_inertia(tmp_path):
    from .zoo import _line, _model

    a, p, mat = _model()
    _line(p.fem, mat, "L100x100x10", node_start=1, el_start=1, y=0.0, name="angle")
    with pytest.raises(IncompatibleElements, match=r"'L100x100x10' \(angle\) has a product of inertia"):
        a.to_fem("angle", "calculix", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True)
