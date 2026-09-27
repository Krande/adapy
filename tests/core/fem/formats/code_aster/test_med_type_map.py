"""MED cell types name a geometry only, so the MED -> adapy direction has to pick one reading."""

import pytest

from ada.fem.formats.code_aster.common import med_to_ada_type
from ada.fem.formats.code_aster.elem_shapes import ada_to_med_format
from ada.fem.shapes.definitions import LineShapes, MassTypes


def test_segment_reads_back_as_a_line():
    # SE2 is shared by LINE, SPRING2 and CONNECTOR; a plain inversion returned CONNECTOR, which a
    # result bake drops, so a Code_Aster beam model rendered its nodes and none of its elements.
    assert med_to_ada_type("SE2") == LineShapes.LINE


def test_point_reads_back_as_a_mass():
    assert med_to_ada_type("PO1") == MassTypes.MASS


@pytest.mark.parametrize("med_type", sorted(set(ada_to_med_format.values())))
def test_every_med_type_round_trips_to_its_own_geometry(med_type):
    assert ada_to_med_format[med_to_ada_type(med_type)] == med_type
