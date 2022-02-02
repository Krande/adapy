import pytest

from ada import FEM
from ada.fem.formats.abaqus.read.reader import get_lcsys_from_bulk


@pytest.fixture
def ori_w_nodes_text():
    return """**
**
*Orientation, name="luffing1_csys", SYSTEM=RECTANGULAR, DEFINITION=NODES
 CRANE-BOOM-1.5,CRANE-1.95,CRANE-1.126
**
**"""


def test_read_orientation_str(ori_w_nodes_text):
    fem = FEM("DummyFEM")
    res = get_lcsys_from_bulk(ori_w_nodes_text, fem)
    assert len(res) == 1
