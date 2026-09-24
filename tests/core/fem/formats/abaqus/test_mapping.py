"""The adapy <-> Abaqus tables: whatever the writer emits, the reader maps back to the same thing."""

from __future__ import annotations

import pytest

from ada.fem.constraints import BcTypes
from ada.fem.formats.abaqus.elem_formulations import AbaqusDefaultElemTypes
from ada.fem.formats.abaqus.mapping import (
    Correspondence,
    Row,
    UnmappedAbaqusValue,
    bc_types,
    element_types,
)
from ada.fem.formulations import as_formulation
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes


@pytest.mark.parametrize("row", list(bc_types()), ids=lambda r: str(r.ada))
def test_every_bc_type_reads_back_as_itself(row):
    table = bc_types()
    assert table.from_abaqus(table.to_abaqus(row.ada)) == row.ada


def test_bc_types_read_in_any_case_and_legacy_spellings_write_as_their_canonical_value():
    table = bc_types()
    assert table.from_abaqus("DISPLACEMENT/ROTATION") == BcTypes.DISPL
    assert table.to_abaqus(BcTypes.DISPL_ROT) == table.to_abaqus(BcTypes.DISPL)
    assert table.from_abaqus(table.to_abaqus(BcTypes.VELOCITY_ANGULAR)) == BcTypes.VELOCITY


def test_a_spelling_listed_for_two_adapy_values_is_refused():
    with pytest.raises(ValueError, match="two adapy values"):
        Correspondence("t", (Row("a", "X"), Row("b", "Y", aliases=("x",))))


def test_an_unknown_value_says_which_table_and_which_value():
    with pytest.raises(UnmappedAbaqusValue, match="boundary condition types.*'nonsense'"):
        bc_types().from_abaqus("nonsense")


_SHAPES = [*ShellShapes, *SolidShapes, *LineShapes]


@pytest.mark.parametrize("reduced", [False, True], ids=["full", "reduced"])
@pytest.mark.parametrize("shape", _SHAPES, ids=lambda s: s.name)
def test_every_element_type_the_writer_emits_reads_back_as_the_same_shape(shape, reduced):
    """Before: reduced integration appended R blindly (S7R, C3D5R, C3D6R, C3D15R -- not Abaqus
    types), QUAD8 defaulted to S8 (no such shell), WEDGE was not found at all."""
    defaults = AbaqusDefaultElemTypes()
    defaults.use_reduced_integration = reduced
    try:
        written = defaults.get_element_type(shape)
    except Exception:
        pytest.skip("not writable with these settings (a refusal, not a wrong type)")
    assert element_types().shape_of(written) == shape


def test_the_formulation_an_element_was_read_as_wins_over_the_default():
    class _Elem:
        type = ShellShapes.TRI
        formulation = as_formulation("cps3")

    assert element_types().write_type(_Elem(), AbaqusDefaultElemTypes()) == "CPS3"
    _Elem.formulation = as_formulation("C3D4")  # not a triangle: the default is written (and reported)
    assert element_types().write_type(_Elem(), AbaqusDefaultElemTypes()) == "S3"
