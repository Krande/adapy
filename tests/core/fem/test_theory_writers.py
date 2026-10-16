"""What the Abaqus, CalculiX and Code_Aster writers write for membrane, truss and cable sections, and what the Abaqus
reader reads back. The solves are in ``tests/fem/test_membrane_truss_cable_solve.py``."""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import LineTheory, LoadPressure, ShellTheory
from ada.fem.exceptions import IncompatibleElements
from ada.fem.formats import conversion_report
from ada.fem.formulations import formulation

from .theory_models import (
    ROD_AREA,
    cable_and_truss,
    membrane_in_tension,
    membrane_square,
    two_bar_truss,
)


def _abaqus(a, tmp_path, name="m") -> str:
    a.to_fem(name, "abaqus", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.inp").read_text()


def _ccx(a, tmp_path, name="m") -> str:
    a.to_fem(name, "calculix", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.inp").read_text()


def _comm(a, tmp_path, name="m") -> str:
    a.to_fem(name, "code_aster", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.comm").read_text()


def _element_types(deck: str) -> list[str]:
    return re.findall(r"\*ELEMENT, type=(\w+)", deck, flags=re.I)


# ---- Abaqus ----------------------------------------------------------------------------------------------------


def test_abaqus_writes_a_membrane_section_on_m3d_elements(tmp_path):
    a, _, _ = membrane_in_tension()
    deck = _abaqus(a, tmp_path)
    assert _element_types(deck) == ["M3D4"]
    assert "** Section: msec\n*Membrane Section, elset=membrane, material=mat\n 0.002," in deck
    assert "*Shell Section" not in deck


def test_abaqus_writes_a_reduced_integration_membrane(tmp_path):
    a, p, _ = membrane_in_tension()
    p.fem.options.ABAQUS.default_elements.use_reduced_integration = True
    assert _element_types(_abaqus(a, tmp_path)) == ["M3D4R"]


def test_a_bending_shell_formulation_is_not_written_on_a_membrane(tmp_path):
    """An element read as S4R keeps that formulation; on a membrane section it is written as the membrane's own type,
    and the change is reported."""
    a, p, _ = membrane_in_tension()
    for el in p.fem.elements:
        el._formulation_override = formulation("abaqus", "S4R")
    with conversion_report.collect() as report:
        deck = _abaqus(a, tmp_path)
    assert _element_types(deck) == ["M3D4"]
    assert any("S4R -> M3D4" in f.subject for f in report.findings)


def test_abaqus_writes_a_truss_and_a_cable(tmp_path):
    a, _ = cable_and_truss(1000.0, pretension=500.0)
    deck = _abaqus(a, tmp_path)
    assert _element_types(deck) == ["T3D2"]
    assert f"*Solid Section, elset=truss, material=mat\n {ROD_AREA!r}," in deck
    assert f"*Solid Section, elset=cable, material=mat_NC\n {ROD_AREA!r}," in deck
    # The cable's tension-only copy of the material, beside the material itself.
    assert "*Material, name=mat\n" in deck and "*Material, name=mat_NC\n" in deck
    nc = deck.split("*Material, name=mat_NC\n")[1].split("*Material")[0]
    assert "*No Compression" in nc
    assert "*No Compression" not in deck.split("*Material, name=mat\n")[1].split("*Material")[0]
    # The pretension as an initial axial stress.
    assert f"*Initial Conditions, type=STRESS\nslack-1.cable, {500.0 / ROD_AREA!r}" in deck


def test_abaqus_writes_a_membrane_pretension(tmp_path):
    a, _, _ = membrane_square(pretension=600.0)
    from ada.fem import StepImplicitStatic

    a.fem.add_step(StepImplicitStatic("s"))
    deck = _abaqus(a, tmp_path)
    assert "*Initial Conditions, type=STRESS\nmemb-1.membrane, 300000.0, 300000.0, 0.0" in deck


def test_abaqus_reads_back_membranes_trusses_and_cables(tmp_path):
    a, _, _ = membrane_in_tension()
    _abaqus(a, tmp_path, "m")
    b = ada.from_fem(tmp_path / "m" / "m.inp")
    (sec,) = [s for p in b.get_all_parts_in_assembly() for s in p.fem.sections]
    assert sec.theory is ShellTheory.MEMBRANE and sec.thickness == 0.002

    a, _ = cable_and_truss(1000.0)
    _abaqus(a, tmp_path, "c")
    b = ada.from_fem(tmp_path / "c" / "c.inp")
    secs = {s.elset.name: s for p in b.get_all_parts_in_assembly() for s in p.fem.sections}
    assert secs["truss"].theory is LineTheory.TRUSS and secs["truss"].area == pytest.approx(ROD_AREA)
    assert secs["cable"].theory is LineTheory.CABLE and secs["cable"].material.name == "mat_NC"


# ---- CalculiX --------------------------------------------------------------------------------------------------


def test_calculix_writes_membranes_and_trusses(tmp_path):
    a, _, _ = membrane_in_tension()
    deck = _ccx(a, tmp_path, "m")
    assert _element_types(deck) == ["M3D4"]
    assert "*Membrane Section, elset=membrane, material=mat\n 0.002," in deck

    a, _, _ = two_bar_truss("truss")
    deck = _ccx(a, tmp_path, "t")
    # No ELSET on the *ELEMENT line: the set is written with the others, and ccx 2.23 crashes on a truss in it twice.
    assert "*ELEMENT, type=T3D2\n" in deck
    assert f"*Solid Section, elset=bars, material=mat\n {ROD_AREA!r}," in deck
    assert "*USER ELEMENT" not in deck


def test_calculix_refuses_a_cable_unless_asked_to_write_it_as_a_truss(tmp_path):
    a, p = cable_and_truss(1000.0)
    with pytest.raises(IncompatibleElements, match="no tension-only truss"):
        _ccx(a, tmp_path, "c")
    p.fem.options.CALCULIX.cable_as_truss = True
    with conversion_report.collect() as report:
        deck = _ccx(a, tmp_path, "c2")
    assert _element_types(deck) == ["T3D2", "T3D2"]
    assert any("cable written as a truss" in f.reason for f in report.findings)


def test_calculix_reports_a_membrane_pretension_it_does_not_write(tmp_path):
    a, _, _ = membrane_square(pretension=600.0)
    with conversion_report.collect() as report:
        deck = _ccx(a, tmp_path)
    assert "*INITIAL CONDITIONS" not in deck.upper()
    assert any("pretension is not written" in f.reason for f in report.findings)


# ---- Code_Aster ------------------------------------------------------------------------------------------------


def test_code_aster_writes_a_membrane(tmp_path):
    a, _, _ = membrane_in_tension()
    comm = _comm(a, tmp_path)
    assert "_F(GROUP_MA=mb_sets, PHENOMENE='MECANIQUE', MODELISATION='MEMBRANE',)," in comm
    assert "mb_sets = ('membrane',)" in comm
    # Small strains: the membrane stiffness, integrated over the thickness, and nothing else.
    a_ = 2.1e11 * 0.002 / (1 - 0.3**2)
    material = comm.split("mm_msec = DEFI_MATERIAU(")[1].split(")\n")[0]
    assert f"M_LLLL={a_!r}" in material and "ELAS=" not in material
    assert "MEMBRANE=(" in comm and "EPAIS=0.002" in comm and "VECT_1=(1.0, 0.0, 0.0)" in comm
    assert "MECA_STATIQUE" in comm


def test_code_aster_writes_a_large_strain_membrane_in_a_nonlinear_step(tmp_path):
    a, _, _ = membrane_in_tension(nl_geom=True)
    comm = _comm(a, tmp_path)
    material = comm.split("mm_msec = DEFI_MATERIAU(")[1].split(")\n")[0]
    assert "ELAS=_F(" in material and "ELAS_MEMBRANE" not in material
    assert '_F(RELATION="ELAS_MEMBRANE_SV", DEFORMATION="GROT_GDEP", GROUP_MA=(\'membrane\',)),' in comm
    assert "ITER_GLOB_MAXI=50" in comm


def test_code_aster_loads_a_membrane_with_pres_rep(tmp_path):
    a, p, _ = membrane_square(pretension=600.0)
    from ada.fem import Bc, FemSet, StepImplicitStatic

    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("all", list(p.fem.nodes), "nset")), [1, 2, 3]))
    step = a.fem.add_step(StepImplicitStatic("s", nl_geom=True))
    step.add_load(LoadPressure("p", 10.0, p.fem.elsets["membrane"]))
    comm = _comm(a, tmp_path)
    assert "PRES_REP=(\n        _F(GROUP_MA='membrane', PRES=10.0),\n    )," in comm
    assert "FORCE_COQUE" not in comm
    assert "N_INIT=600.0" in comm


def test_code_aster_writes_trusses_and_cables(tmp_path):
    a, _ = cable_and_truss(1000.0, pretension=250.0, compression_ratio=1e-3)
    with conversion_report.collect() as report:
        comm = _comm(a, tmp_path)
    assert "_F(GROUP_MA=tr_sets, PHENOMENE='MECANIQUE', MODELISATION='BARRE',)," in comm
    assert "_F(GROUP_MA=cb_sets, PHENOMENE='MECANIQUE', MODELISATION='CABLE',)," in comm
    assert f"SECTION='GENERALE',\n                CARA='A',\n                VALE={ROD_AREA!r}," in comm
    assert f"AIRE={ROD_AREA!r},\n                N_INIT=250.0," in comm
    assert "CABLE=_F(EC_SUR_E=0.001)" in comm
    assert "POUTRE=()" in comm and "ORIENTATION" not in comm
    # A cable is solved nonlinearly even in a linear step.
    assert "STAT_NON_LINE" in comm and "MECA_STATIQUE" not in comm
    assert '_F(RELATION="CABLE", DEFORMATION="GROT_GDEP", GROUP_MA=(\'cable\',)),' in comm
    assert any("CABLE is nonlinear only" in f.reason for f in report.findings)
