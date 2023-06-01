import pytest

from ada import FEM, Node
from ada.fem.formats.abaqus.read.read_orientations import get_lcsys_from_bulk


@pytest.fixture
def ori_w_nodes_text():
    return """**
**
*Orientation, name="dummy_csys", DEFINITION=NODES, SYSTEM=RECTANGULAR
 DummyFEM.5,DummyFEM.95,DummyFEM.126
**
**"""


def test_read_orientation_str(ori_w_nodes_text):
    fem = FEM("DummyFEM")
    fem.nodes.add(Node((0, 0, 0), 5))
    fem.nodes.add(Node((1, 0, 0), 95))
    fem.nodes.add(Node((1, 1, 0), 126))
    res = get_lcsys_from_bulk(ori_w_nodes_text, fem)
    assert len(res) == 1
    csys = res["dummy_csys"]
    assert csys.definition == "NODES"
    assert len(csys.nodes) == 3
