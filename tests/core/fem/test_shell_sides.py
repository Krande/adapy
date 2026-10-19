"""Issue #412: a shell surface's side is read one way by every writer.

The Abaqus writer used to test ``== -1`` alone, so the string ``"SNEG"`` -- which the Sesam,
Calculix and Code_Aster writers all took as the negative face -- fell through to ``SPOS``: one
``Surface``, two different faces in two decks, with nothing raised. These tests run each spelling
of a side through each writer, and an invalid one, which must be refused rather than defaulted
to the positive face.
"""

import pytest

import ada
from ada.fem import Elem, FemSet, Surface
from ada.fem.containers import FemElements
from ada.fem.exceptions.model_definition import UnsupportedLoadType
from ada.fem.formats.abaqus.write.write_surfaces import surface_str
from ada.fem.formats.calculix.write import write_loads as ccx_loads
from ada.fem.formats.code_aster.write import write_loads as aster_loads
from ada.fem.formats.sesam.write.write_loads import (
    SIDE_NEGATIVE,
    SIDE_POSITIVE,
    load_pressure,
)
from ada.fem.loads import LoadPressure
from ada.fem.shapes.definitions import ShellShapes
from ada.fem.shell_sides import UnsupportedShellSide, normalise_shell_side

NEGATIVE = [-1, "SNEG"]
POSITIVE = [1, "SPOS"]
INVALID = [0, 2, "SMID", "S1", "sneg", 1.5]
#: What the Abaqus writer refuses: ``INVALID`` less ``0``, which it takes as Abaqus' 0-based first face.
ABAQUS_INVALID = [s for s in INVALID if s != 0]
Q = 1000.0


def _shell_surface(side) -> Surface:
    nodes = [ada.Node((k * 0.1, k * 0.2, 0.0), k + 1) for k in range(4)]
    el = Elem(1, nodes, ShellShapes.QUAD)
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.elements = FemElements([el], fem_obj=fem)
    el.parent = fem
    es = fem.add_set(FemSet("SHELLS", [el], FemSet.TYPES.ELSET, parent=fem))
    return Surface("SURF", Surface.TYPES.ELEMENT, es, el_face_index=side, parent=fem)


# --- the shared helper ---------------------------------------------------------------------


@pytest.mark.parametrize("side", POSITIVE + [None, ""])
def test_helper_positive_spellings(side):
    assert normalise_shell_side(side) == 1


@pytest.mark.parametrize("side", NEGATIVE)
def test_helper_negative_spellings(side):
    assert normalise_shell_side(side) == -1


@pytest.mark.parametrize("side", INVALID)
def test_helper_refuses_anything_else(side):
    with pytest.raises(UnsupportedShellSide, match="not a shell surface side"):
        normalise_shell_side(side)


def test_helper_refuses_an_unhashable_side_rather_than_raising_typeerror():
    with pytest.raises(UnsupportedShellSide):
        normalise_shell_side({"SNEG"})


# --- Abaqus: the writer the issue is about -------------------------------------------------


@pytest.mark.parametrize("side", NEGATIVE)
def test_abaqus_writes_the_negative_face_as_sneg(side):
    out = surface_str(_shell_surface(side), False)
    assert "SHELLS, SNEG" in out
    assert "SPOS" not in out


@pytest.mark.parametrize("side", POSITIVE)
def test_abaqus_writes_the_positive_face_as_spos(side):
    out = surface_str(_shell_surface(side), False)
    assert "SHELLS, SPOS" in out
    assert "SNEG" not in out


@pytest.mark.parametrize("side", ABAQUS_INVALID)
def test_abaqus_refuses_an_unknown_side_instead_of_writing_spos(side):
    with pytest.raises(UnsupportedShellSide):
        surface_str(_shell_surface(side), False)


def test_abaqus_keeps_taking_face_index_0_as_spos():
    """0 is Abaqus' 0-based first face (SPOS is a shell's face 1); the model zoo builds shell
    surfaces with it, and it must not start raising. It is not a side for the pressure writers."""
    out = surface_str(_shell_surface(0), False)
    assert "SHELLS, SPOS" in out
    assert "SNEG" not in out


def test_abaqus_writes_every_side_of_a_multi_set_surface_by_its_own_face():
    s1 = _shell_surface("SNEG")
    s2 = _shell_surface("SPOS")
    surf = Surface("M", Surface.TYPES.ELEMENT, [s1.fem_set, s2.fem_set], el_face_index=["SNEG", 0], parent=s1.parent)
    lines = surface_str(surf, False).splitlines()
    assert [ln for ln in lines if ln.startswith("SHELLS")] == ["SHELLS, SNEG", "SHELLS, SPOS"]


# --- the pressure writers: every spelling, and one reading across all four -----------------


@pytest.mark.parametrize("side", NEGATIVE)
def test_sesam_negative_side(side):
    surf = _shell_surface(side)
    out = load_pressure(LoadPressure("q", Q, surf), 1)
    assert [float(x) for x in out.splitlines()[1].split()][3] == float(SIDE_NEGATIVE)


@pytest.mark.parametrize("side", POSITIVE)
def test_sesam_positive_side(side):
    surf = _shell_surface(side)
    out = load_pressure(LoadPressure("q", Q, surf), 1)
    assert [float(x) for x in out.splitlines()[1].split()][3] == float(SIDE_POSITIVE)


@pytest.mark.parametrize("side", INVALID)
def test_sesam_refuses_an_unknown_side(side):
    surf = _shell_surface(side)
    # BEUSLO cannot carry it: nothing is written, and the load is reported as dropped.
    assert load_pressure(LoadPressure("q", Q, surf), 1) == ""


@pytest.mark.parametrize("module", [ccx_loads, aster_loads], ids=["calculix", "code_aster"])
def test_calculix_and_code_aster_signs(module):
    positive = module.PRESSURE_SIGN["positive"]
    negative = module.PRESSURE_SIGN["negative"]
    for side in POSITIVE:
        assert module._pressure_sign(side) == positive
    for side in NEGATIVE:
        assert module._pressure_sign(side) == negative


@pytest.mark.parametrize("module", [ccx_loads, aster_loads], ids=["calculix", "code_aster"])
@pytest.mark.parametrize("side", INVALID)
def test_calculix_and_code_aster_refuse_an_unknown_side(module, side):
    with pytest.raises(UnsupportedLoadType):
        module._pressure_sign(side)


@pytest.mark.parametrize("side", NEGATIVE + POSITIVE)
def test_one_surface_names_one_face_in_every_deck(side):
    """The cross-format property the issue is about: Abaqus, Sesam, Calculix and Code_Aster agree."""
    negative = normalise_shell_side(side) == -1
    surf = _shell_surface(side)

    assert ("SHELLS, SNEG" in surface_str(surf, False)) is negative
    sesam_side = [float(x) for x in load_pressure(LoadPressure("q", Q, surf), 1).splitlines()[1].split()][3]
    assert (sesam_side == SIDE_NEGATIVE) is negative
    assert (ccx_loads._pressure_sign(side) == ccx_loads.PRESSURE_SIGN["negative"]) is negative
    assert (aster_loads._pressure_sign(side) == aster_loads.PRESSURE_SIGN["negative"]) is negative
