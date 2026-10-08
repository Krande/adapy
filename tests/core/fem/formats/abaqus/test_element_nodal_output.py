"""``FieldOutput(element_nodal=...)``: element variables at the element nodes (Abaqus POSITION=NODES)."""

from ada.fem import FieldOutput
from ada.fem.formats.abaqus.write.write_output_requests import field_output_str


def test_element_nodal_variables_get_their_own_request():
    deck = field_output_str(FieldOutput("f", nodal=["U"], element=["S"], contact=[], element_nodal=["E", "LE"]))
    assert "*Element Output, directions=YES\n S\n*Element Output, position=NODES\n E, LE" in deck


def test_no_element_nodal_request_by_default():
    deck = field_output_str(FieldOutput("f", nodal=["U"], element=["S"], contact=[]))
    assert "position=NODES" not in deck
